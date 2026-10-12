from __future__ import annotations

from threading import RLock
from typing import Any
from uuid import UUID

from action_runtime.capability_registry import CapabilityRegistry
from action_runtime.domain.action_session import ActionOutcome, WaitingReason
from action_runtime.services.action_execution import (
    ActionExecutionService,
    ConfirmationAuthorizationDeniedError,
    ConfirmationConflictError,
    ConfirmationForbiddenError,
)
from action_runtime.services.action_sessions import (
    SessionNotFoundError,
    SessionRecord,
    SessionUpdateError,
    SessionVersionConflictError,
)
from action_runtime.services.capability_discovery import CapabilityDiscoveryService
from action_runtime.services.conversations import ConversationService
from action_runtime.services.delegated_identity import (
    DelegatedActionContext,
    DelegatedIdentityError,
    DelegatedIdentityResolver,
)
from action_runtime.services.identity_parameter_binding import IdentityParameterBindingError
from action_runtime.services.intent_interpretation import (
    IntentInterpretationService,
    IntentProviderError,
    InvalidIntentOutputError,
)
from action_runtime.services.response_handling import ResponseHandlingService

from .contracts import (
    DelegatedUserContext,
    SubAgentTaskResult,
    SubAgentTaskState,
)


class TaskAccessError(LookupError):
    def __init__(self) -> None:
        super().__init__("Task is unavailable")


class SubAgentTaskService:
    """Runs subagent tasks on top of the existing ActionSession lifecycle.

    This service deliberately has no dependency on A2A. Protocol adapters translate
    their messages into ``handle`` calls and translate the returned state back out.
    """

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        intent_service: IntentInterpretationService,
        discovery_service: CapabilityDiscoveryService,
        sessions: ActionExecutionService,
        identity_resolver: DelegatedIdentityResolver,
    ) -> None:
        self.registry = registry
        self.intent_service = intent_service
        self.discovery_service = discovery_service
        self.sessions = sessions
        self.identity_resolver = identity_resolver
        self._identity_lock = RLock()
        self._task_identities: dict[str, tuple[str, DelegatedUserContext]] = {}
        self._context_identities: dict[str, DelegatedUserContext] = {}
        self.response_handling = ResponseHandlingService()
        self._session_ids_by_task: dict[str, UUID] = {}
        self.conversations = ConversationService()
        self._conversation_ids_by_task: dict[str, UUID] = {}

    def assert_context_access(self, *, context_id: str, user_context: DelegatedUserContext) -> None:
        with self._identity_lock:
            existing = self._context_identities.get(context_id)
            if existing is not None and existing != user_context:
                raise TaskAccessError()

    def bind_task(
        self, *, task_id: str, context_id: str, user_context: DelegatedUserContext
    ) -> None:
        snapshot = DelegatedUserContext.model_validate(user_context.model_dump())
        with self._identity_lock:
            self.assert_context_access(context_id=context_id, user_context=snapshot)
            existing = self._task_identities.get(task_id)
            if existing is not None and existing != (context_id, snapshot):
                raise TaskAccessError()
            self._task_identities[task_id] = (context_id, snapshot)
            self._context_identities[context_id] = snapshot

    def assert_task_access(
        self,
        *,
        task_id: str,
        user_id: str,
        context_id: str | None = None,
        user_context: DelegatedUserContext | None = None,
    ) -> None:
        with self._identity_lock:
            binding = self._task_identities.get(task_id)
            if (
                binding is None
                or binding[1].user_id != user_id
                or (context_id is not None and binding[0] != context_id)
                or (user_context is not None and binding[1] != user_context)
            ):
                raise TaskAccessError()

    def _delegation(self, task_id: str) -> DelegatedActionContext:
        with self._identity_lock:
            return DelegatedActionContext(
                self._task_identities[task_id][1].model_copy(deep=True), self.identity_resolver
            )

    def handle(
        self,
        *,
        task_id: str,
        context_id: str,
        trace_id: str,
        user_context: DelegatedUserContext,
        text: str = "",
        data: dict[str, Any] | None = None,
    ) -> SubAgentTaskResult:
        """Start or continue a task using text or a structured continuation command."""
        self.bind_task(task_id=task_id, context_id=context_id, user_context=user_context)
        delegation = self._delegation(task_id)
        session_id = self._session_ids_by_task.get(task_id)
        if session_id is None:
            return self._start(
                task_id=task_id,
                context_id=context_id,
                trace_id=trace_id,
                text=text,
                delegation=delegation,
            )

        try:
            record = self.sessions.get(session_id)
        except SessionNotFoundError:
            self._session_ids_by_task.pop(task_id, None)
            return self._failed(
                "The action session for this task is no longer available.",
                code="action_session_not_found",
                task_id=task_id,
            )

        if record.session.is_terminal:
            return self._from_record(record)

        try:
            if data:
                record = self._continue_with_data(record=record, data=data, delegation=delegation)
            else:
                if (
                    record.session.waiting_reason is WaitingReason.USER_CONFIRMATION
                    and text.strip().casefold()
                    in {"confirm", "confirmed", "approve", "yes", "cancel", "reject", "no"}
                ):
                    record = self._continue_with_text(
                        record=record, text=text, delegation=delegation
                    )
                else:
                    return self._start(
                        task_id=task_id,
                        context_id=context_id,
                        trace_id=trace_id,
                        text=text,
                        delegation=delegation,
                    )
        except IdentityParameterBindingError as error:
            return self._binding_error(task_id, error)
        except DelegatedIdentityError:
            return self._failed(
                "Delegated authorization is unavailable",
                code="authorization_denied",
                task_id=task_id,
                rejected=True,
            )
        except (SessionVersionConflictError, ConfirmationConflictError) as error:
            return self._failed(
                str(error),
                code="task_version_conflict",
                task_id=task_id,
                retryable=True,
            )
        except (ConfirmationForbiddenError, ConfirmationAuthorizationDeniedError) as error:
            return self._failed(
                str(error),
                code="authorization_denied",
                task_id=task_id,
                rejected=True,
            )
        except IntentProviderError:
            return self._failed(
                "Intent interpretation is temporarily unavailable.",
                code="intent_provider_unavailable",
                task_id=task_id,
                retryable=True,
            )
        except InvalidIntentOutputError:
            return self._failed(
                "The intent provider returned an invalid result.",
                code="invalid_intent_output",
                task_id=task_id,
                retryable=True,
            )
        except SessionUpdateError as error:
            return self._failed(
                str(error),
                code="invalid_task_continuation",
                task_id=task_id,
            )

        return self._from_record(record)

    def cancel(self, *, task_id: str, user_id: str) -> SubAgentTaskResult:
        """Cancel a task when its ActionSession is awaiting confirmation."""
        self.assert_task_access(task_id=task_id, user_id=user_id)
        delegation = self._delegation(task_id)
        session_id = self._session_ids_by_task.get(task_id)
        if session_id is None:
            return self._failed(
                "The task has no active action session.",
                code="task_not_cancelable",
                task_id=task_id,
            )
        try:
            record = self.sessions.get(session_id)
        except SessionNotFoundError:
            return self._failed(
                "The action session for this task is no longer available.",
                code="action_session_not_found",
                task_id=task_id,
            )
        if record.session.is_terminal:
            return self._from_record(record)
        if (
            record.session.waiting_reason is not WaitingReason.USER_CONFIRMATION
            or record.confirmation is None
        ):
            return self._failed(
                "The task can only be canceled while waiting for confirmation.",
                code="task_not_cancelable",
                task_id=task_id,
            )
        try:
            record = self.sessions.decide_confirmation(
                session_id=record.session.id,
                confirmation_id=record.confirmation.id,
                expected_version=record.session.version,
                principal=delegation.current_principal(),
                delegation=delegation,
                confirmed=False,
            )
        except (
            ConfirmationAuthorizationDeniedError,
            ConfirmationConflictError,
            ConfirmationForbiddenError,
            SessionUpdateError,
            SessionVersionConflictError,
        ) as error:
            return self._failed(
                str(error),
                code="task_cancel_failed",
                task_id=task_id,
            )
        return self._from_record(record)

    def _start(
        self,
        *,
        task_id: str,
        context_id: str,
        trace_id: str,
        text: str,
        delegation: DelegatedActionContext,
    ) -> SubAgentTaskResult:
        if not text.strip():
            return self._failed(
                "A text request is required to start an action task.",
                code="request_text_required",
                task_id=task_id,
            )
        try:
            turn = self.conversations.handle(
                principal=delegation.current_principal(),
                text=text,
                trace_id=trace_id,
                sessions=self.sessions,
                intent_service=self.intent_service,
                discovery_service=self.discovery_service,
                conversation_id=self._conversation_ids_by_task.get(task_id),
                allow_new_action=False,
                delegation=delegation,
            )
            self._conversation_ids_by_task[task_id] = turn.conversation_id
            interpretation = turn.interpretation
        except IdentityParameterBindingError as error:
            return self._binding_error(task_id, error)
        except DelegatedIdentityError:
            return self._failed(
                "Delegated authorization is unavailable",
                code="authorization_denied",
                task_id=task_id,
                rejected=True,
            )
        except SessionVersionConflictError as error:
            return self._failed(
                str(error), code="task_version_conflict", task_id=task_id, retryable=True
            )
        except SessionUpdateError as error:
            return self._failed(str(error), code="invalid_task_continuation", task_id=task_id)
        except IntentProviderError:
            return self._failed(
                "Intent interpretation is temporarily unavailable.",
                code="intent_provider_unavailable",
                task_id=task_id,
                retryable=True,
            )
        except InvalidIntentOutputError:
            return self._failed(
                "The intent provider returned an invalid result.",
                code="invalid_intent_output",
                task_id=task_id,
                retryable=True,
            )

        if interpretation.outcome == "capability_discovery":
            discovery = turn.discovery
            return SubAgentTaskResult(
                state=SubAgentTaskState.COMPLETED,
                message="Available actions were discovered.",
                artifact_name="capability-discovery",
                data={
                    "type": "capability_discovery",
                    "task_id": task_id,
                    "discovery": discovery.model_dump(mode="json"),
                },
            )

        if interpretation.outcome == "clarification_required":
            return SubAgentTaskResult(
                state=SubAgentTaskState.INPUT_REQUIRED,
                message=interpretation.message or "Please clarify the requested action.",
                data={
                    "type": "clarification_required",
                    "task_id": task_id,
                    "input_kind": "text",
                },
            )

        if interpretation.outcome == "unsupported":
            return self._failed(
                interpretation.message or "The requested action is not supported.",
                code="unsupported_action",
                task_id=task_id,
                rejected=True,
            )

        record = turn.record
        if record is None:
            return self._failed(
                "No action session was produced", code="session_update_failed", task_id=task_id
            )
        self._session_ids_by_task[task_id] = record.session.id
        return self._from_record(record)

    def _continue_with_data(
        self,
        *,
        record: SessionRecord,
        data: dict[str, Any],
        delegation: DelegatedActionContext,
    ) -> SessionRecord:
        operation = data.get("operation")
        expected_version = data.get("expected_version")
        if isinstance(expected_version, float) and expected_version.is_integer():
            expected_version = int(expected_version)
        if type(expected_version) is not int:
            raise SessionUpdateError("expected_version must be an integer")

        if operation == "provide_parameters":
            parameters = data.get("parameters")
            if not isinstance(parameters, dict) or not parameters:
                raise SessionUpdateError("provide_parameters requires a nonempty parameters object")
            return self.sessions.add_parameters(
                session_id=record.session.id,
                expected_version=expected_version,
                parameters=parameters,
                principal=delegation.current_principal(),
                delegation=delegation,
            )

        if operation == "confirmation":
            if record.confirmation is None:
                raise SessionUpdateError("the task is not waiting for confirmation")
            decision = data.get("decision")
            if decision not in {"confirm", "cancel"}:
                raise SessionUpdateError("confirmation decision must be confirm or cancel")
            supplied_confirmation_id = data.get("confirmation_id")
            if not isinstance(supplied_confirmation_id, str):
                raise SessionUpdateError("confirmation_id must be a UUID")
            try:
                confirmation_id = UUID(supplied_confirmation_id)
            except ValueError as error:
                raise SessionUpdateError("confirmation_id must be a UUID") from error
            return self.sessions.decide_confirmation(
                session_id=record.session.id,
                confirmation_id=confirmation_id,
                expected_version=expected_version,
                principal=delegation.current_principal(),
                delegation=delegation,
                confirmed=decision == "confirm",
            )

        raise SessionUpdateError("operation must be provide_parameters or confirmation")

    def _continue_with_text(
        self, *, record: SessionRecord, text: str, delegation: DelegatedActionContext
    ) -> SessionRecord:
        normalized = text.strip()
        if not normalized:
            raise SessionUpdateError("a continuation message is required")

        if record.session.waiting_reason is WaitingReason.USER_CONFIRMATION:
            decisions = {
                "confirm": True,
                "confirmed": True,
                "approve": True,
                "yes": True,
                "cancel": False,
                "reject": False,
                "no": False,
            }
            decision = decisions.get(normalized.casefold())
            if decision is None or record.confirmation is None:
                raise SessionUpdateError("reply with confirm or cancel")
            if decision:
                raise SessionUpdateError(
                    "Confirm using the structured confirmation_id and expected_version"
                )
            return self.sessions.decide_confirmation(
                session_id=record.session.id,
                confirmation_id=record.confirmation.id,
                expected_version=record.session.version,
                principal=delegation.current_principal(),
                delegation=delegation,
                confirmed=decision,
            )

        raise SessionUpdateError("the task is not waiting for input")

    def _binding_error(
        self, task_id: str, error: IdentityParameterBindingError
    ) -> SubAgentTaskResult:
        session_id = self._session_ids_by_task.get(task_id)
        if session_id is None:
            return self._failed(str(error), code=error.code, task_id=task_id, rejected=True)
        result = self._from_record(self.sessions.get(session_id))
        return SubAgentTaskResult(
            state=SubAgentTaskState.INPUT_REQUIRED,
            message=str(error),
            data={**result.data, "code": error.code},
        )

    def _from_record(self, record: SessionRecord) -> SubAgentTaskResult:
        session = record.session
        entry = self.registry.resolve(
            application_id=session.application_id or "",
            capability_id=session.capability_id or "",
            version=session.capability_version,
        )
        visible_parameters = dict(session.collected_parameters)
        confirmation = None
        if record.confirmation is not None:
            confirmation = {
                "confirmation_id": str(record.confirmation.id),
                "summary": record.confirmation_summary,
                "risk_level": record.confirmation.risk_level.value,
                "status": record.confirmation.status.value,
            }
        result = self.response_handling.create_result(record=record, entry=entry)
        payload = {
            "type": "action_session",
            "action_session_id": str(session.id),
            "conversation_id": str(session.conversation_id),
            "version": session.version,
            "stage": session.stage.value,
            "stage_status": session.stage_status.value,
            "outcome": session.outcome.value,
            "waiting_reason": session.waiting_reason.value if session.waiting_reason else None,
            "application_id": session.application_id,
            "capability_id": session.capability_id,
            "capability_version": session.capability_version,
            "parameters": visible_parameters,
            "missing_parameters": list(record.missing_parameters),
            "validation_issues": list(record.validation_issues),
            "confirmation": confirmation,
            "result": result.model_dump(mode="json") if result is not None else None,
            "trace_id": session.trace_id,
        }

        if session.outcome is ActionOutcome.COMPLETED:
            return SubAgentTaskResult(
                state=SubAgentTaskState.COMPLETED,
                message="The action completed successfully.",
                data=payload,
            )
        if session.outcome is ActionOutcome.CANCELLED:
            return SubAgentTaskResult(
                state=SubAgentTaskState.CANCELED,
                message="The action was canceled.",
                data=payload,
            )
        if session.outcome is ActionOutcome.DENIED:
            return SubAgentTaskResult(
                state=SubAgentTaskState.REJECTED,
                message="The action was not authorized.",
                data=payload,
            )
        if session.outcome is not ActionOutcome.PENDING:
            return SubAgentTaskResult(
                state=SubAgentTaskState.FAILED,
                message=(
                    session.last_error.message
                    if session.last_error is not None
                    else "The action did not complete successfully."
                ),
                data=payload,
            )

        if session.waiting_reason is WaitingReason.USER_CONFIRMATION:
            payload["input_kind"] = "confirmation"
            return SubAgentTaskResult(
                state=SubAgentTaskState.INPUT_REQUIRED,
                message=record.confirmation_summary or "Confirm or cancel this action.",
                data=payload,
            )

        payload["input_kind"] = "parameters"
        return SubAgentTaskResult(
            state=SubAgentTaskState.INPUT_REQUIRED,
            message="Additional action parameters are required.",
            data=payload,
        )

    @staticmethod
    def _failed(
        message: str,
        *,
        code: str,
        task_id: str,
        retryable: bool = False,
        rejected: bool = False,
    ) -> SubAgentTaskResult:
        return SubAgentTaskResult(
            state=(SubAgentTaskState.REJECTED if rejected else SubAgentTaskState.FAILED),
            message=message,
            artifact_name="subagent-error",
            data={
                "type": "error",
                "task_id": task_id,
                "code": code,
                "retryable": retryable,
            },
        )
