from __future__ import annotations

from action_runtime.capability_registry import RegisteredCapability
from action_runtime.contracts.action_status import (
    ActionStatusResponse,
    ActionTrackingStatus,
)
from action_runtime.domain.action_session import (
    ActionOutcome,
    ActionStage,
    WaitingReason,
)
from action_runtime.domain.execution import ExecutionStatus
from action_runtime.services.action_sessions import SessionRecord


class ActionStatusService:
    """Maps internal orchestration state to the stable FR-15 tracking contract."""

    def create_status(
        self,
        *,
        record: SessionRecord,
        entry: RegisteredCapability,
    ) -> ActionStatusResponse:
        session = record.session
        tracking_status = self._tracking_status(record)
        error = session.last_error
        return ActionStatusResponse(
            action_id=session.id,
            transaction_id=self.transaction_id(record=record, entry=entry),
            status=tracking_status,
            outcome=session.outcome.value,
            stage=session.stage.value,
            stage_status=session.stage_status.value,
            waiting_reason=(session.waiting_reason.value if session.waiting_reason else None),
            application_id=entry.capability.application_id,
            capability_id=entry.capability.id,
            capability_version=entry.capability.version,
            api_version=entry.capability.api_version,
            input_schema_version=entry.capability.input_schema_version,
            input_schema_hash=session.parameter_schema_hash or "",
            output_schema_version=entry.capability.output_schema_version,
            output_schema_hash=session.output_schema_hash or "",
            submitted_at=session.created_at,
            processing_started_at=(
                record.execution.started_at if record.execution is not None else None
            ),
            completed_at=session.updated_at if session.is_terminal else None,
            updated_at=session.updated_at,
            poll_after_seconds=(None if session.is_terminal else 2),
            error=(
                {
                    "code": error.code,
                    "message": error.message,
                    "stage": error.stage.value,
                    "retryable": error.retryable,
                }
                if error is not None
                else None
            ),
        )

    @staticmethod
    def transaction_id(
        *,
        record: SessionRecord,
        entry: RegisteredCapability,
    ) -> str | None:
        execution = record.execution
        field_name = entry.capability.response_handling.transaction_id_field
        if execution is None or field_name is None:
            return None
        value = execution.response_data.get(field_name)
        return None if value is None else str(value)

    @staticmethod
    def _tracking_status(record: SessionRecord) -> ActionTrackingStatus:
        session = record.session
        if session.outcome is ActionOutcome.COMPLETED:
            return ActionTrackingStatus.COMPLETED
        if session.is_terminal:
            return ActionTrackingStatus.FAILED

        execution = record.execution
        if (
            execution is not None
            and execution.status in {ExecutionStatus.CREATED, ExecutionStatus.RUNNING}
        ) or session.stage in {
            ActionStage.EXECUTE,
            ActionStage.VERIFY,
            ActionStage.AUDIT,
        }:
            return ActionTrackingStatus.PROCESSING
        if session.waiting_reason in {
            WaitingReason.EXTERNAL_SYSTEM,
            WaitingReason.RETRY_SCHEDULED,
        }:
            return ActionTrackingStatus.PROCESSING
        return ActionTrackingStatus.SUBMITTED
