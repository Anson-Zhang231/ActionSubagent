from __future__ import annotations

from typing import Any

from action_runtime.capability_registry import RegisteredCapability
from action_runtime.contracts.action_result import StandardActionResult
from action_runtime.domain.action_session import ActionOutcome
from action_runtime.services.action_sessions import SessionRecord


class ResponseHandlingService:
    """Creates stable, user-facing results from terminal Action Sessions."""

    def create_result(
        self,
        *,
        record: SessionRecord,
        entry: RegisteredCapability,
    ) -> StandardActionResult | None:
        session = record.session
        if session.outcome is ActionOutcome.PENDING:
            return None

        response = dict(record.execution.response_data) if record.execution else {}
        if session.outcome is ActionOutcome.COMPLETED:
            handling = entry.capability.response_handling
            transaction_id = self._optional_text(response, handling.transaction_id_field)
            status = self._optional_text(response, handling.status_field) or session.outcome.value
            message = handling.success_message_template.format_map(response)
            return StandardActionResult(
                success=True,
                transaction_id=transaction_id,
                status=status.upper(),
                message=message,
                error_code=None,
                retryable=False,
                data=response,
            )

        error = session.last_error
        return StandardActionResult(
            success=False,
            transaction_id=None,
            status=session.outcome.value,
            message=self._failure_message(record),
            error_code=error.code.upper() if error is not None else None,
            retryable=error.retryable if error is not None else False,
            data={},
        )

    @staticmethod
    def _optional_text(response: dict[str, Any], field_name: str | None) -> str | None:
        if field_name is None:
            return None
        value = response.get(field_name)
        return None if value is None else str(value)

    @staticmethod
    def _failure_message(record: SessionRecord) -> str:
        outcome = record.session.outcome
        if outcome is ActionOutcome.CANCELLED:
            return "The action was cancelled before execution."
        if outcome is ActionOutcome.DENIED:
            return "You are not authorized to perform this action."
        if outcome is ActionOutcome.TIMED_OUT:
            return "The action timed out before a result was confirmed."
        if outcome is ActionOutcome.INDETERMINATE:
            return (
                "The action may have been processed, but its final status could not be confirmed."
            )
        if record.session.last_error is not None:
            return record.session.last_error.message
        return "The action could not be completed."
