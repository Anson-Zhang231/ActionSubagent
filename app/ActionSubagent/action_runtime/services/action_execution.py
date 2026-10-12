from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPException
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request
from uuid import UUID, uuid4

from action_runtime.application_registry import ApplicationNotFoundError, ApplicationRegistry
from action_runtime.capability_registry import RegisteredCapability, canonical_hash
from action_runtime.contracts.action_status import ActionStatusResponse
from action_runtime.domain.action_session import ActionError, ActionStage
from action_runtime.domain.audit_event import AuditEvent
from action_runtime.domain.authorization import AuthenticatedPrincipal
from action_runtime.domain.capability import MappingSourceType
from action_runtime.domain.confirmation import ConfirmationStatus
from action_runtime.domain.execution import Execution, ExecutionStatus
from action_runtime.orchestrator.events import ActionEventType
from action_runtime.services.action_sessions import (
    InMemoryActionSessionService,
    SessionNotFoundError,
    SessionRecord,
    SessionUpdateError,
    SessionVersionConflictError,
)
from action_runtime.services.action_status import ActionStatusService
from action_runtime.services.application_credentials import (
    ApplicationCredentialAccessDeniedError,
    ApplicationCredentialError,
    EnvironmentApplicationCredentialResolver,
)
from action_runtime.services.audit_trail import AuditTrailService
from action_runtime.services.authorization import AuthorizationService
from action_runtime.services.data_mapping import (
    DataMappingError,
    DataMappingService,
    MappedHttpRequest,
)
from action_runtime.services.delegated_identity import DelegatedActionContext
from action_runtime.services.error_handling import ErrorHandlingService
from action_runtime.services.http_transport import open_no_redirect
from action_runtime.services.identity_parameter_binding import IdentityParameterBinder
from action_runtime.services.master_data_contract import MasterDataValidator
from action_runtime.services.parameter_validation import validate_parameters

logger = logging.getLogger("agent-api.execution")


class ConfirmationConflictError(ValueError):
    pass


class ConfirmationForbiddenError(PermissionError):
    pass


class ConfirmationAuthorizationDeniedError(PermissionError):
    pass


class TransactionIdConflictError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ConnectorResponse:
    status: int | None
    data: dict[str, Any]
    request_sent: bool
    timed_out: bool = False
    transport_error: str | None = None
    external_request_id: str | None = None
    response_error: str | None = None
    outcome_unknown: bool = False


class HttpCapabilityConnector:
    def __init__(
        self,
        *,
        application_registry: ApplicationRegistry,
        credential_resolver: EnvironmentApplicationCredentialResolver | None = None,
        simulation_header: str | None = None,
    ) -> None:
        self.application_registry = application_registry
        self.credential_resolver = credential_resolver or EnvironmentApplicationCredentialResolver()
        self.simulation_header = simulation_header

    def execute(
        self,
        *,
        entry: RegisteredCapability,
        mapped_request: MappedHttpRequest,
        user_id: str,
        trace_id: str,
        idempotency_key: str,
        simulation: str | None = None,
    ) -> ConnectorResponse:
        definition = entry.capability.execution
        path = mapped_request.path
        query = mapped_request.query
        if query:
            path = f"{path}?{urlencode(query)}"
        body = mapped_request.body
        try:
            application = self.application_registry.resolve(
                application_id=entry.capability.application_id
            ).application
            authentication_headers = self.credential_resolver.headers_for(
                application.connection.authentication,
                user_id=user_id,
            )
        except ApplicationNotFoundError:
            return ConnectorResponse(
                status=None,
                data={},
                request_sent=False,
                transport_error="The registered Application is unavailable",
            )
        except ApplicationCredentialAccessDeniedError:
            return ConnectorResponse(
                status=None,
                data={},
                request_sent=False,
                transport_error="No Application credential is configured for this user",
            )
        except ApplicationCredentialError:
            logger.exception("Application credential resolution failed")
            return ConnectorResponse(
                status=None,
                data={},
                request_sent=False,
                transport_error="Application credential configuration is invalid",
            )
        headers = {
            "Content-Type": "application/json",
            "X-Trace-ID": trace_id,
            "Idempotency-Key": idempotency_key,
            **authentication_headers,
        }
        if simulation and self.simulation_header:
            headers[self.simulation_header] = simulation
        url = application.connection.base_url.rstrip("/") + path
        payload = (
            json.dumps(body, separators=(",", ":")).encode()
            if definition.method.value != "GET" or definition.send_body_on_get
            else None
        )

        return self._send(
            url=url,
            method=definition.method.value,
            payload=payload,
            headers=headers,
            timeout=definition.timeout_seconds,
        )

    @staticmethod
    def _send(
        *, url: str, method: str, payload: bytes | None, headers: dict[str, str], timeout: float
    ) -> ConnectorResponse:
        request = Request(url, data=payload, headers=headers, method=method)
        started_at = time.perf_counter()
        phase = "open"

        def log_failure(error: BaseException, category: str) -> None:
            print(
                "UPSTREAM_FAILURE",
                json.dumps(
                    {
                        "trace_id": headers.get("X-Trace-ID"),
                        "method": request.get_method(),
                        "url": request.full_url,
                        "category": category,
                        "phase": phase,
                        "exception_type": type(error).__name__,
                        "reason_type": type(error.reason).__name__
                        if isinstance(error, URLError)
                        else None,
                        "timeout_seconds": timeout,
                        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 3),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        print(
            "UPSTREAM_REQUEST",
            json.dumps(
                {
                    "trace_id": headers.get("X-Trace-ID"),
                    "method": request.get_method(),
                    "url": request.full_url,
                    "body": json.loads(payload) if payload else None,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        try:
            with open_no_redirect(request, timeout=timeout) as response:
                phase = "read"
                raw = response.read()
                try:
                    data = json.loads(raw) if raw else {}
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return ConnectorResponse(
                        status=response.status,
                        data={},
                        request_sent=True,
                        external_request_id=response.headers.get("X-Request-ID"),
                        response_error="non_json_response",
                    )
                print(
                    "UPSTREAM_RESPONSE",
                    json.dumps(
                        {
                            "trace_id": headers.get("X-Trace-ID"),
                            "http_status": response.status,
                            "upstream_request_id": response.headers.get("X-Request-ID"),
                            "body": data,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return ConnectorResponse(
                    status=response.status,
                    data=data if isinstance(data, dict) else {"value": data},
                    request_sent=True,
                    external_request_id=response.headers.get("X-Request-ID"),
                )
        except HTTPError as error:
            try:
                raw = error.read()
            except (HTTPException, OSError):
                raw = b""
            try:
                data = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                data = {}
            print(
                "UPSTREAM_RESPONSE",
                json.dumps(
                    {
                        "trace_id": headers.get("X-Trace-ID"),
                        "http_status": error.code,
                        "upstream_request_id": error.headers.get("X-Request-ID"),
                        "body": data,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            timed_out = error.code == 504
            return ConnectorResponse(
                status=error.code,
                data=data if isinstance(data, dict) else {},
                request_sent=True,
                timed_out=timed_out,
            )
        except TimeoutError as error:
            log_failure(error, "timed_out")
            return ConnectorResponse(None, {}, True, timed_out=True)
        except URLError as error:
            log_failure(error, "transport_error")
            return ConnectorResponse(
                None,
                {},
                False,
                transport_error=type(error.reason).__name__,
            )
        except (HTTPException, OSError) as error:
            log_failure(error, "outcome_unknown")
            return ConnectorResponse(None, {}, True, outcome_unknown=True)


class ActionExecutionService(InMemoryActionSessionService):
    def __init__(
        self,
        registry: Any,
        connector: HttpCapabilityConnector | None = None,
        application_registry: ApplicationRegistry | None = None,
        connector_simulation_header: str | None = None,
        master_data_validator: MasterDataValidator | None = None,
        authorization_service: AuthorizationService | None = None,
        data_mapping_service: DataMappingService | None = None,
        identity_parameter_binder: IdentityParameterBinder | None = None,
        authorization_mode: Literal["local", "supervisor"] = "local",
    ) -> None:
        super().__init__(
            registry,
            master_data_validator=master_data_validator,
            authorization_service=authorization_service,
            identity_parameter_binder=identity_parameter_binder,
            authorization_mode=authorization_mode,
        )
        if connector is None:
            if application_registry is None:
                raise ValueError(
                    "application_registry is required when no execution connector is supplied"
                )
            connector = HttpCapabilityConnector(
                application_registry=application_registry,
                simulation_header=connector_simulation_header,
            )
        self.connector = connector
        self.data_mapping_service = data_mapping_service or DataMappingService()
        self.error_handling_service = ErrorHandlingService()
        self.audit_trail_service = AuditTrailService()
        self.action_status_service = ActionStatusService()

    def get_action_status(
        self,
        *,
        action_id: UUID | None = None,
        transaction_id: str | None = None,
    ) -> tuple[SessionRecord, ActionStatusResponse]:
        if (action_id is None) == (transaction_id is None):
            raise ValueError("provide exactly one of action_id or transaction_id")
        # Status reads intentionally do not acquire the orchestration lock. A long-running
        # connector call holds that lock while mutating the Action, and polling must still be
        # able to observe the already-published RUNNING execution state.
        if action_id is not None:
            try:
                record = self._records[action_id]
            except KeyError as error:
                raise SessionNotFoundError(str(action_id)) from error
        else:
            matches = [
                candidate
                for candidate in tuple(self._records.values())
                if self._transaction_id(candidate) == transaction_id
            ]
            if not matches:
                raise SessionNotFoundError(str(transaction_id))
            if len(matches) > 1:
                raise TransactionIdConflictError(str(transaction_id))
            record = matches[0]
        session = record.session
        entry = self.registry.resolve(
            application_id=session.application_id or "",
            capability_id=session.capability_id or "",
            version=session.capability_version,
        )
        return record, self.action_status_service.create_status(record=record, entry=entry)

    def _transaction_id(self, record: SessionRecord) -> str | None:
        session = record.session
        entry = self.registry.resolve(
            application_id=session.application_id or "",
            capability_id=session.capability_id or "",
            version=session.capability_version,
        )
        return self.action_status_service.transaction_id(record=record, entry=entry)

    def decide_confirmation(
        self,
        *,
        session_id: UUID,
        confirmation_id: UUID,
        expected_version: int,
        principal: AuthenticatedPrincipal,
        confirmed: bool,
        simulation: str | None = None,
        delegation: DelegatedActionContext | None = None,
    ) -> SessionRecord:
        with self._lock:
            record = self.get(session_id)
            self._check_delegation(principal=principal, delegation=delegation, record=record)
            session = record.session
            if session.version != expected_version:
                raise SessionVersionConflictError(
                    f"expected version {expected_version}, current version is {session.version}"
                )
            confirmation = record.confirmation
            if confirmation is None or confirmation.id != confirmation_id:
                raise ConfirmationConflictError("confirmation does not belong to this session")
            if confirmation.user_id != principal.user_id or session.user_id != principal.user_id:
                raise ConfirmationForbiddenError("only the session owner can decide confirmation")
            if not confirmation.is_pending:
                raise ConfirmationConflictError("confirmation has already been resolved")
            now = datetime.now(UTC)
            if now >= confirmation.expires_at:
                confirmation.status = ConfirmationStatus.EXPIRED
                confirmation.resolved_at = now
                confirmation.version += 1
                self._advance(session, ActionEventType.CONFIRMATION_EXPIRED)
                self._write_final_audit(record, actor_id="system")
                return record

            if confirmed and self.authorization_mode == "local":
                if self.authorization_service is None:
                    raise ConfirmationAuthorizationDeniedError(
                        "authorization service is not configured"
                    )
                entry = self.registry.resolve(
                    application_id=session.application_id or "",
                    capability_id=session.capability_id or "",
                    version=session.capability_version,
                )
                if delegation is not None:
                    principal = delegation.current_principal()
                decision = self.authorization_service.reauthorize(
                    decision_id=session.authorization_decision_id or "",
                    principal=principal,
                    entry=entry,
                    parameters=session.collected_parameters,
                    trace_id=session.trace_id,
                )
                if not decision.allowed:
                    raise ConfirmationAuthorizationDeniedError("authorization is no longer valid")
                session.authorization_decision_id = decision.id
                record.authorization_reason_code = decision.reason_code

            confirmation.status = (
                ConfirmationStatus.CONFIRMED if confirmed else ConfirmationStatus.CANCELLED
            )
            confirmation.resolved_at = now
            confirmation.resolved_by = principal.user_id
            confirmation.version += 1
            confirmation.validate_invariants()
            if not confirmed:
                self._advance(session, ActionEventType.ACTION_CANCELLED)
                self._write_final_audit(record, actor_id=principal.user_id)
                return record

            self._advance(session, ActionEventType.ACTION_CONFIRMED)
            self._execute(record, simulation=simulation)
            return record

    def list_audit_events(self, session_id: UUID) -> list[AuditEvent]:
        return list(self.get(session_id).audit_events)

    def verify_audit_chain(self, session_id: UUID) -> bool:
        return self.audit_trail_service.verify_chain(self.list_audit_events(session_id))

    def _on_authorization_denied(self, record: SessionRecord) -> None:
        self._write_final_audit(record, actor_id="authorization_service")

    def _on_confirmation_skipped(
        self,
        record: SessionRecord,
        *,
        principal: AuthenticatedPrincipal,
        delegation: DelegatedActionContext | None = None,
    ) -> None:
        session = record.session
        if self.authorization_mode == "supervisor":
            self._execute(record, simulation=None)
            return
        if self.authorization_service is None:
            self._deny_revoked_authorization(record, "authorization_service_unavailable")
            return
        entry = self.registry.resolve(
            application_id=session.application_id or "",
            capability_id=session.capability_id or "",
            version=session.capability_version,
        )
        if delegation is not None:
            principal = delegation.current_principal()
        decision = self.authorization_service.reauthorize(
            decision_id=session.authorization_decision_id or "",
            principal=principal,
            entry=entry,
            parameters=session.collected_parameters,
            trace_id=session.trace_id,
        )
        if not decision.allowed:
            self._deny_revoked_authorization(record, decision.reason_code)
            return
        session.authorization_decision_id = decision.id
        record.authorization_reason_code = decision.reason_code
        self._execute(record, simulation=None)

    def _deny_revoked_authorization(
        self,
        record: SessionRecord,
        reason_code: str,
    ) -> None:
        session = record.session
        record.authorization_reason_code = reason_code
        session.last_error = ActionError(
            code="authorization_denied",
            message="Authorization is no longer valid for this action",
            stage=ActionStage.AUTHORIZE,
            details={"reason_code": reason_code},
        )
        self._advance(session, ActionEventType.AUTHORIZATION_REVOKED)
        self._write_final_audit(record, actor_id="authorization_service")

    def _execute(self, record: SessionRecord, *, simulation: str | None) -> None:
        session = record.session
        entry = self.registry.resolve(
            application_id=session.application_id or "",
            capability_id=session.capability_id or "",
            version=session.capability_version,
        )
        definition = entry.capability.execution
        request_hash = canonical_hash(
            {
                "method": definition.method.value,
                "path": definition.path,
                "parameters": session.collected_parameters,
            }
        )
        execution = Execution.create(
            session_id=session.id,
            confirmation_id=session.confirmation_id,
            application_id=entry.capability.application_id,
            capability_id=entry.capability.id,
            capability_version=entry.capability.version,
            connector=definition.connector,
            http_method=definition.method,
            request_path=definition.path,
            request_hash=request_hash,
            idempotency_key=f"action-{session.id}",
            attempt=1,
            timeout_seconds=definition.timeout_seconds,
        )
        record.execution = execution
        session.execution_id = execution.id
        self._advance(session, ActionEventType.EXECUTION_STARTED)
        execution.status = ExecutionStatus.RUNNING
        execution.started_at = datetime.now(UTC)
        execution.version += 1

        try:
            mapped_request = self.data_mapping_service.map_request(
                entry,
                session.collected_parameters,
            )
        except DataMappingError as error:
            self._fail_mapping(record, error, code="request_mapping_failed")
            return
        execution.request_path = mapped_request.path
        execution.request_hash = canonical_hash(
            {
                "method": definition.method.value,
                "path": mapped_request.path,
                "query": mapped_request.query,
                "body": mapped_request.body,
            }
        )

        response = ConnectorResponse(
            status=None,
            data={},
            request_sent=False,
            transport_error="request_not_attempted",
        )
        retry_policy = definition.retry_policy
        for attempt in range(1, retry_policy.max_attempts + 1):
            execution.attempt = attempt
            response = self.connector.execute(
                entry=entry,
                mapped_request=mapped_request,
                user_id=session.user_id,
                trace_id=session.trace_id,
                idempotency_key=execution.idempotency_key,
                simulation=simulation,
            )
            execution.request_sent = execution.request_sent or response.request_sent
            execution.external_request_id = response.external_request_id
            execution.version += 1
            retry_reason = self._retry_reason(response, entry)
            if retry_reason is None or attempt >= retry_policy.max_attempts:
                break
            delay = min(30.0, retry_policy.backoff_seconds * (2 ** (attempt - 1)))
            logger.warning(
                "execution_retry_scheduled",
                extra={
                    "trace_id": session.trace_id,
                    "application_id": entry.capability.application_id,
                    "capability_id": entry.capability.id,
                    "attempt": attempt,
                    "max_attempts": retry_policy.max_attempts,
                    "retry_reason": retry_reason,
                },
            )
            if delay > 0:
                time.sleep(delay)

        execution.finished_at = datetime.now(UTC)
        application_error = None
        if response.outcome_unknown:
            execution.status = ExecutionStatus.UNKNOWN
            self._advance(session, ActionEventType.EXECUTION_STATUS_UNKNOWN)
        elif response.timed_out:
            execution.status = ExecutionStatus.TIMED_OUT
            self._advance(session, ActionEventType.EXECUTION_TIMED_OUT)
        elif response.transport_error:
            execution.status = ExecutionStatus.FAILED
            execution.error = ActionError(
                code="connector_unavailable",
                message="The target system could not be reached",
                stage=ActionStage.EXECUTE,
                retryable=True,
                details={"reason": response.transport_error},
            )
            session.last_error = execution.error
            self._advance(session, ActionEventType.EXECUTION_FAILED)
            execution.validate_invariants()
            self._write_final_audit(record, actor_id="orchestrator")
            return
        else:
            fallback_message_fields = tuple(
                str(rule.source)
                for rule in entry.capability.execution.response_mapping.values()
                if rule.source_type is MappingSourceType.RESPONSE
            )
            if response.status is not None and response.status >= 300:
                application_error = self.error_handling_service.from_application_response(
                    status=response.status,
                    body=response.data,
                    retryable_http_statuses=retry_policy.retryable_http_statuses,
                    fallback_message_fields=fallback_message_fields,
                )
                mapped_response = {}
            elif response.response_error:
                application_error = ActionError(
                    code="invalid_application_response",
                    message="The target application returned an invalid JSON response",
                    stage=ActionStage.EXECUTE,
                    details={"http_status": response.status},
                )
                mapped_response = {}
            elif self._has_business_message_error(entry, response.data):
                application_error = self.error_handling_service.from_application_response(
                    status=response.status,
                    body=response.data,
                    retryable_http_statuses=retry_policy.retryable_http_statuses,
                    fallback_message_fields=fallback_message_fields,
                )
                mapped_response = {}
            else:
                try:
                    mapped_response = self.data_mapping_service.map_response(entry, response.data)
                except DataMappingError as error:
                    self._fail_mapping(record, error, code="response_mapping_failed")
                    return
            execution.status = ExecutionStatus.RETURNED
            execution.http_status = response.status
            execution.response_data = mapped_response
            self._advance(session, ActionEventType.EXECUTION_RETURNED)
        execution.validate_invariants()
        self._verify(
            record,
            entry,
            application_error=(application_error if not response.timed_out else None),
        )

    @staticmethod
    def _has_business_message_error(entry: RegisteredCapability, response: dict[str, Any]) -> bool:
        expected = entry.capability.verification.expected_field_values
        mapping = entry.capability.execution.response_mapping.get("message")
        if (
            "message" not in expected
            or mapping is None
            or mapping.source_type is not MappingSourceType.RESPONSE
        ):
            return False
        found, value = DataMappingService._get_path(response, str(mapping.source))
        return not found or value != expected["message"]

    def _fail_mapping(
        self,
        record: SessionRecord,
        error: DataMappingError,
        *,
        code: str,
    ) -> None:
        execution = record.execution
        if execution is None:
            raise SessionUpdateError("execution record is missing")
        execution.status = ExecutionStatus.FAILED
        execution.finished_at = datetime.now(UTC)
        execution.error = ActionError(
            code=code,
            message="Capability data mapping could not be applied",
            stage=ActionStage.EXECUTE,
            retryable=False,
            details={"reason": str(error)},
        )
        execution.version += 1
        record.session.last_error = execution.error
        self._advance(record.session, ActionEventType.EXECUTION_FAILED)
        execution.validate_invariants()
        self._write_final_audit(record, actor_id="data_mapping_service")

    @staticmethod
    def _retry_reason(
        response: ConnectorResponse,
        entry: RegisteredCapability,
    ) -> str | None:
        policy = entry.capability.execution.retry_policy
        if response.timed_out and policy.retry_on_timeout:
            return "timeout"
        if response.transport_error and policy.retry_on_transport_error:
            return "transport_error"
        if response.status in policy.retryable_http_statuses:
            return f"http_{response.status}"
        return None

    def _verify(
        self,
        record: SessionRecord,
        entry: RegisteredCapability,
        *,
        application_error: ActionError | None = None,
    ) -> None:
        session = record.session
        execution = record.execution
        if execution is None:
            raise SessionUpdateError("execution record is missing")
        self._advance(session, ActionEventType.VERIFICATION_STARTED)
        verification = entry.capability.verification
        output_issues = validate_parameters(
            dict(entry.capability.output_schema),
            dict(execution.response_data),
        )
        if application_error is not None:
            session.last_error = application_error
            self._advance(session, ActionEventType.VERIFICATION_FAILED)
        elif execution.status in {ExecutionStatus.TIMED_OUT, ExecutionStatus.UNKNOWN}:
            session.last_error = ActionError(
                code="execution_status_unknown",
                message="The target system did not return a verifiable result",
                stage=ActionStage.VERIFY,
                retryable=True,
            )
            self._advance(session, ActionEventType.VERIFICATION_INDETERMINATE)
        elif output_issues:
            session.last_error = ActionError(
                code="output_schema_validation_failed",
                message="The target response did not match the versioned output schema",
                stage=ActionStage.VERIFY,
                details={"issues": output_issues},
            )
            self._advance(session, ActionEventType.VERIFICATION_FAILED)
        else:
            missing = [
                field
                for field in verification.required_response_fields
                if field not in execution.response_data
            ]
            mismatched = [
                field
                for field, expected in verification.expected_field_values.items()
                if execution.response_data.get(field) != expected
            ]
            if (
                execution.http_status not in verification.accepted_http_statuses
                or missing
                or mismatched
            ):
                session.last_error = ActionError(
                    code="verification_failed",
                    message="The target response did not satisfy the Capability contract",
                    stage=ActionStage.VERIFY,
                    details={
                        "http_status": execution.http_status,
                        "missing_fields": missing,
                        "mismatched_fields": mismatched,
                    },
                )
                self._advance(session, ActionEventType.VERIFICATION_FAILED)
            else:
                self._advance(session, ActionEventType.VERIFICATION_PASSED)
        self._write_final_audit(record, actor_id="verification_engine")

    def _write_final_audit(self, record: SessionRecord, *, actor_id: str) -> None:
        session = record.session
        source_stage = session.stage
        source_status = session.stage_status
        final_error = session.last_error
        self._advance(session, ActionEventType.AUDIT_STARTED)
        event_id = uuid4()
        occurred_at = datetime.now(UTC)
        self._advance(session, ActionEventType.AUDIT_WRITTEN)
        session.last_error = final_error
        session.validate_invariants()
        entry = self.registry.resolve(
            application_id=session.application_id or "",
            capability_id=session.capability_id or "",
            version=session.capability_version,
        )
        record.audit_events.append(
            self.audit_trail_service.create_final_event(
                record=record,
                entry=entry,
                actor_id=actor_id,
                action_event_id=event_id,
                source_stage=source_stage,
                source_status=source_status,
                final_error=final_error,
                occurred_at=occurred_at,
            )
        )


__all__ = [
    "ConfirmationConflictError",
    "ConfirmationAuthorizationDeniedError",
    "ConfirmationForbiddenError",
    "ConnectorResponse",
    "HttpCapabilityConnector",
    "ActionExecutionService",
    "SessionNotFoundError",
    "TransactionIdConflictError",
]
