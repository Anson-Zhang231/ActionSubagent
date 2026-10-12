from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Literal
from uuid import UUID, uuid4

from action_runtime.capability_registry import (
    CapabilityRegistry,
    RegisteredCapability,
    canonical_hash,
)
from action_runtime.domain.action_session import (
    ActionError,
    ActionSession,
    ActionStage,
)
from action_runtime.domain.audit_event import AuditEvent
from action_runtime.domain.authorization import AuthenticatedPrincipal
from action_runtime.domain.confirmation import Confirmation, ConfirmationStatus
from action_runtime.domain.delegation import DelegatedUserContext
from action_runtime.domain.execution import Execution
from action_runtime.orchestrator.events import ActionEventType
from action_runtime.orchestrator.transition import get_transition_rule
from action_runtime.services.authorization import AuthorizationService
from action_runtime.services.business_rule_validation import validate_business_rules
from action_runtime.services.delegated_identity import DelegatedActionContext
from action_runtime.services.identity_parameter_binding import IdentityParameterBinder
from action_runtime.services.master_data_contract import (
    MasterDataLookupUnavailableError,
    MasterDataValidator,
)
from action_runtime.services.parameter_validation import validate_parameters


class SessionNotFoundError(LookupError):
    pass


class SessionVersionConflictError(ValueError):
    pass


class SessionUpdateError(ValueError):
    pass


@dataclass(slots=True)
class SessionRecord:
    session: ActionSession
    user_request: str = ""
    input_parameters: dict[str, Any] = field(default_factory=dict)
    missing_parameters: list[str] = field(default_factory=list)
    validation_issues: list[dict[str, str]] = field(default_factory=list)
    confirmation_summary: str | None = None
    confirmation: Confirmation | None = None
    execution: Execution | None = None
    audit_events: list[AuditEvent] = field(default_factory=list)
    authorization_reason_code: str | None = None
    delegated_user_context: DelegatedUserContext | None = None


class InMemoryActionSessionService:
    def __init__(
        self,
        registry: CapabilityRegistry,
        master_data_validator: MasterDataValidator | None = None,
        authorization_service: AuthorizationService | None = None,
        identity_parameter_binder: IdentityParameterBinder | None = None,
        authorization_mode: Literal["local", "supervisor"] = "local",
    ) -> None:
        if authorization_mode not in {"local", "supervisor"}:
            raise ValueError("authorization_mode must be local or supervisor")
        self.registry = registry
        self.master_data_validator = master_data_validator
        self.authorization_service = authorization_service
        self.identity_parameter_binder = identity_parameter_binder
        self.authorization_mode = authorization_mode
        self._records: dict[UUID, SessionRecord] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        principal: AuthenticatedPrincipal,
        trace_id: str,
        application_id: str,
        capability_id: str,
        capability_version: str | None,
        parameters: dict[str, Any],
        conversation_id: UUID | None,
        user_request: str | None = None,
        delegation: DelegatedActionContext | None = None,
    ) -> SessionRecord:
        entry = self.registry.resolve(
            application_id=application_id,
            capability_id=capability_id,
            version=capability_version,
        )
        if delegation is not None:
            self._check_delegation(principal=principal, delegation=delegation)
            parameters = self._bind_parameters(
                entry=entry, parameters=parameters, delegation=delegation
            )
        session = ActionSession.start(
            conversation_id=conversation_id or uuid4(),
            user_id=principal.user_id,
            trace_id=trace_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        record = SessionRecord(
            session=session,
            user_request=(
                user_request
                if user_request is not None
                else f"API request for {application_id}.{capability_id}"
            ),
            input_parameters=dict(parameters),
            delegated_user_context=(
                delegation.user_context.model_copy(deep=True) if delegation is not None else None
            ),
        )
        with self._lock:
            self._advance(session, ActionEventType.UNDERSTANDING_STARTED)
            self._advance(session, ActionEventType.INTENT_UNDERSTOOD)
            self._advance(session, ActionEventType.CAPABILITY_DISCOVERY_STARTED)
            session.application_id = entry.capability.application_id
            session.capability_id = entry.capability.id
            session.capability_version = entry.capability.version
            session.api_version = entry.capability.api_version
            session.input_schema_version = entry.capability.input_schema_version
            session.output_schema_version = entry.capability.output_schema_version
            session.parameter_schema_hash = canonical_hash(entry.capability.input_schema)
            session.output_schema_hash = canonical_hash(entry.capability.output_schema)
            session.collected_parameters = dict(parameters)
            self._advance(session, ActionEventType.CAPABILITY_SELECTED)
            self._process_parameters(
                record, entry=entry, principal=principal, delegation=delegation
            )
            self._records[session.id] = record
        return record

    def get(self, session_id: UUID) -> SessionRecord:
        with self._lock:
            try:
                return self._records[session_id]
            except KeyError as error:
                raise SessionNotFoundError(str(session_id)) from error

    def add_parameters(
        self,
        *,
        session_id: UUID,
        expected_version: int,
        parameters: dict[str, Any],
        principal: AuthenticatedPrincipal,
        removed_parameters: list[str] | None = None,
        delegation: DelegatedActionContext | None = None,
    ) -> SessionRecord:
        with self._lock:
            record = self.get(session_id)
            session = record.session
            self._check_delegation(principal=principal, delegation=delegation, record=record)
            if session.user_id != principal.user_id:
                raise SessionUpdateError("only the session owner can add parameters")
            if session.version != expected_version:
                raise SessionVersionConflictError(
                    f"expected version {expected_version}, current version is {session.version}"
                )
            if datetime.now(UTC) >= session.expires_at:
                raise SessionUpdateError("session has expired")
            if not session.is_waiting or session.stage not in {
                ActionStage.COLLECT_DATA,
                ActionStage.CONFIRM,
            }:
                raise SessionUpdateError("session is not waiting for parameter data")
            if delegation is not None:
                candidate = {
                    k: v
                    for k, v in session.collected_parameters.items()
                    if k not in (removed_parameters or [])
                }
                candidate.update(parameters)
                entry = self.registry.resolve(
                    application_id=session.application_id or "",
                    capability_id=session.capability_id or "",
                    version=session.capability_version,
                )
                parameters = self._bind_parameters(
                    entry=entry,
                    parameters=candidate,
                    delegation=delegation,
                    removed_parameters=removed_parameters,
                )
            if session.stage is ActionStage.CONFIRM:
                if record.confirmation is not None:
                    record.confirmation.status = ConfirmationStatus.INVALIDATED
                    record.confirmation.resolved_at = datetime.now(UTC)
                    record.confirmation.resolved_by = principal.user_id
                    record.confirmation.version += 1
                self._advance(session, ActionEventType.ACTION_MODIFICATION_REQUESTED)
            self._advance(session, ActionEventType.PARAMETER_DATA_RECEIVED)
            for name in removed_parameters or []:
                record.input_parameters.pop(name, None)
                session.collected_parameters.pop(name, None)
            record.input_parameters.update(parameters)
            session.collected_parameters.update(parameters)
            session.validated_parameter_hash = None
            session.authorization_decision_id = None
            session.confirmation_id = None
            record.confirmation_summary = None
            record.confirmation = None
            self._process_parameters(
                record,
                entry=self.registry.resolve(
                    application_id=session.application_id or "",
                    capability_id=session.capability_id or "",
                    version=session.capability_version,
                ),
                principal=principal,
                delegation=delegation,
            )
            return record

    def _check_delegation(
        self,
        *,
        principal: AuthenticatedPrincipal,
        delegation: DelegatedActionContext | None,
        record: SessionRecord | None = None,
    ) -> None:
        if record is not None and record.delegated_user_context is not None:
            if delegation is None or delegation.user_context != record.delegated_user_context:
                raise SessionUpdateError("Delegated session identity cannot change")
        if delegation is not None:
            identity = delegation.user_context
            if (principal.user_id, principal.organization_id, principal.department_id) != (
                identity.user_id,
                identity.organization_id,
                identity.department_id,
            ):
                raise SessionUpdateError("Delegated principal does not match the session identity")

    def _bind_parameters(
        self,
        *,
        entry: RegisteredCapability,
        parameters: dict[str, Any],
        delegation: DelegatedActionContext,
        removed_parameters: list[str] | None = None,
    ) -> dict[str, Any]:
        if self.identity_parameter_binder is None:
            raise SessionUpdateError("Delegated identity parameter binding is not configured")
        return self.identity_parameter_binder.bind(
            entry=entry,
            parameters=parameters,
            user_context=delegation.user_context,
            rules=delegation.resolver.binding_rules(),
            removed_parameters=removed_parameters,
        )

    def _process_parameters(
        self,
        record: SessionRecord,
        *,
        entry: RegisteredCapability,
        principal: AuthenticatedPrincipal,
        delegation: DelegatedActionContext | None = None,
    ) -> None:
        session = record.session
        schema = entry.capability.input_schema
        self._advance(session, ActionEventType.DATA_COLLECTION_STARTED)
        required = schema.get("required", [])
        record.missing_parameters = [
            name
            for name in required
            if name not in session.collected_parameters
            or session.collected_parameters[name] is None
        ]
        if record.missing_parameters:
            record.validation_issues = []
            self._advance(session, ActionEventType.REQUIRED_DATA_MISSING)
            return

        self._advance(session, ActionEventType.REQUIRED_DATA_COMPLETE)
        self._advance(session, ActionEventType.VALIDATION_STARTED)
        record.validation_issues = validate_parameters(schema, session.collected_parameters)
        if not record.validation_issues:
            record.validation_issues = validate_business_rules(
                entry.capability.business_rules,
                session.collected_parameters,
            )
        if not record.validation_issues and entry.capability.master_data_rules:
            if self.master_data_validator is None:
                raise MasterDataLookupUnavailableError("Master Data validation is not configured")
            record.validation_issues = self.master_data_validator.validate(
                application_id=entry.capability.application_id,
                rules=entry.capability.master_data_rules,
                parameters=session.collected_parameters,
                user_id=session.user_id,
                trace_id=session.trace_id,
            )
        if record.validation_issues:
            session.last_error = ActionError(
                code="validation_failed",
                message="One or more parameters are invalid",
                stage=ActionStage.VALIDATE,
                details={"fields": [issue["field"] for issue in record.validation_issues]},
            )
            self._advance(session, ActionEventType.VALIDATION_FAILED)
            return

        parameter_hash = canonical_hash(session.collected_parameters)
        session.validated_parameter_hash = parameter_hash
        self._advance(session, ActionEventType.VALIDATION_PASSED)
        self._advance(session, ActionEventType.AUTHORIZATION_STARTED)
        if self.authorization_mode == "supervisor":
            # Lifecycle correlation marker, not a locally verified authorization decision.
            session.authorization_decision_id = f"supervisor-managed:{session.id}"
            record.authorization_reason_code = "supervisor_managed"
        else:
            if self.authorization_service is None:
                raise SessionUpdateError("authorization service is not configured")
            if delegation is not None:
                principal = delegation.current_principal()
            decision = self.authorization_service.authorize(
                principal=principal,
                entry=entry,
                parameters=session.collected_parameters,
                trace_id=session.trace_id,
            )
            session.authorization_decision_id = decision.id
            record.authorization_reason_code = decision.reason_code
            if not decision.allowed:
                session.last_error = ActionError(
                    code="authorization_denied",
                    message="The user is not authorized to perform this action",
                    stage=ActionStage.AUTHORIZE,
                    details={"reason_code": decision.reason_code},
                )
                self._advance(session, ActionEventType.AUTHORIZATION_DENIED)
                self._on_authorization_denied(record)
                return

        self._advance(session, ActionEventType.AUTHORIZATION_GRANTED)
        if not entry.capability.confirmation.required:
            self._advance(session, ActionEventType.CONFIRMATION_SKIPPED)
            self._on_confirmation_skipped(record, principal=principal, delegation=delegation)
            return

        record.confirmation_summary = _render_confirmation(entry, session.collected_parameters)
        risk_level = entry.capability.confirmation.risk_level
        if risk_level is None:
            raise SessionUpdateError("required confirmation is missing a risk level")
        confirmation = Confirmation.request(
            session_id=session.id,
            user_id=session.user_id,
            application_id=entry.capability.application_id,
            capability_id=entry.capability.id,
            capability_version=entry.capability.version,
            authorization_decision_id=session.authorization_decision_id or "",
            parameter_hash=session.validated_parameter_hash or "",
            summary_hash=canonical_hash(record.confirmation_summary),
            token_hash=canonical_hash(str(uuid4())),
            risk_level=risk_level,
            expires_at=min(session.expires_at, datetime.now(UTC) + timedelta(minutes=10)),
        )
        record.confirmation = confirmation
        session.confirmation_id = confirmation.id
        self._advance(session, ActionEventType.CONFIRMATION_REQUESTED)

    def _on_authorization_denied(self, record: SessionRecord) -> None:
        del record

    def _on_confirmation_skipped(
        self,
        record: SessionRecord,
        *,
        principal: AuthenticatedPrincipal,
        delegation: DelegatedActionContext | None = None,
    ) -> None:
        del record, principal, delegation

    @staticmethod
    def _advance(session: ActionSession, event_type: ActionEventType) -> None:
        rule = get_transition_rule(
            stage=session.stage,
            status=session.stage_status,
            event_type=event_type,
        )
        if rule.target_stage is not None:
            session.stage = rule.target_stage
        session.stage_status = rule.target_status
        session.waiting_reason = rule.waiting_reason
        if rule.pending_outcome is not None:
            session.pending_outcome = rule.pending_outcome
        if rule.finalize_outcome:
            if session.pending_outcome is None:
                raise SessionUpdateError("cannot finalize a session without a pending outcome")
            session.outcome = session.pending_outcome
            session.pending_outcome = None
        if rule.clear_error:
            session.last_error = None
        session.version += 1
        session.updated_at = datetime.now(UTC)
        session.validate_invariants()


def _render_confirmation(entry: RegisteredCapability, parameters: dict[str, Any]) -> str:
    template = entry.capability.confirmation.message_template
    if template is None:
        raise SessionUpdateError("required confirmation is missing a message template")
    try:
        return template.format_map(parameters)
    except KeyError as error:
        raise SessionUpdateError(
            f"confirmation template references unknown input: {error}"
        ) from error


def parameter_hash(parameters: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
