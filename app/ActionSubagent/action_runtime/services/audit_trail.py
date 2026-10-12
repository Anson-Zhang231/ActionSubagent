from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from action_runtime.capability_registry import RegisteredCapability
from action_runtime.domain.action_session import ActionError, ActionStage, StageStatus
from action_runtime.domain.audit_event import AuditEvent
from action_runtime.services.action_sessions import SessionRecord


class AuditTrailService:
    """Builds audit records with original values and verifies their append-only hash chain."""

    def create_final_event(
        self,
        *,
        record: SessionRecord,
        entry: RegisteredCapability,
        actor_id: str,
        action_event_id: UUID,
        source_stage: ActionStage,
        source_status: StageStatus,
        final_error: ActionError | None,
        occurred_at: datetime,
    ) -> AuditEvent:
        session = record.session
        execution = record.execution
        input_parameters = dict(record.input_parameters)
        validated_parameters = dict(session.collected_parameters)
        execution_result = dict(execution.response_data) if execution else {}
        user_request = record.user_request
        response_handling = entry.capability.response_handling
        transaction_id = self._optional_text(
            execution_result,
            response_handling.transaction_id_field,
        )
        business_status = self._optional_text(
            execution_result,
            response_handling.status_field,
        )
        execution_time_ms = None
        if execution and execution.started_at and execution.finished_at:
            execution_time_ms = round(
                (execution.finished_at - execution.started_at).total_seconds() * 1000,
                3,
            )
        error_message = final_error.message if final_error is not None else None
        redacted_fields: tuple[str, ...] = ()
        payload = {
            "action_id": str(session.id),
            "contract_versions": {
                "capability_version": session.capability_version,
                "api_version": session.api_version,
                "input_schema_version": session.input_schema_version,
                "input_schema_hash": session.parameter_schema_hash,
                "output_schema_version": session.output_schema_version,
                "output_schema_hash": session.output_schema_hash,
            },
            "user_request": user_request,
            "input_parameters": input_parameters,
            "validated_parameters": validated_parameters,
            "execution_result": execution_result,
            "transaction_id": transaction_id,
            "status": (business_status or session.outcome.value).upper(),
            "error": (
                {
                    "code": final_error.code,
                    "message": error_message,
                    "stage": final_error.stage.value,
                    "retryable": final_error.retryable,
                }
                if final_error
                else None
            ),
            "execution_time_ms": execution_time_ms,
            "execution_id": str(session.execution_id) if session.execution_id else None,
            "authorization_decision_id": session.authorization_decision_id,
            "authorization_reason_code": record.authorization_reason_code,
        }
        previous_hash = record.audit_events[-1].event_hash if record.audit_events else None
        hash_input = self._hash_input(
            sequence_number=len(record.audit_events) + 1,
            session_id=session.id,
            conversation_id=session.conversation_id,
            action_event_id=action_event_id,
            user_id=session.user_id,
            actor_id=actor_id,
            event_source="AUDIT_SERVICE",
            event_type="AUDIT_WRITTEN",
            trace_id=session.trace_id,
            correlation_id=session.trace_id,
            causation_id=None,
            source_stage=source_stage,
            source_status=source_status,
            target_stage=session.stage,
            target_status=session.stage_status,
            outcome=session.outcome.value,
            pending_outcome=(session.pending_outcome.value if session.pending_outcome else None),
            application_id=session.application_id,
            capability_id=session.capability_id,
            capability_version=session.capability_version,
            payload_summary=payload,
            redacted_fields=redacted_fields,
            previous_event_hash=previous_hash,
            occurred_at=occurred_at,
        )
        event_hash = self._digest(hash_input)
        return AuditEvent.create(
            sequence_number=len(record.audit_events) + 1,
            session_id=session.id,
            conversation_id=session.conversation_id,
            action_event_id=action_event_id,
            user_id=session.user_id,
            actor_id=actor_id,
            event_source="AUDIT_SERVICE",
            event_type="AUDIT_WRITTEN",
            trace_id=session.trace_id,
            correlation_id=session.trace_id,
            causation_id=None,
            source_stage=source_stage,
            source_status=source_status,
            target_stage=session.stage,
            target_status=session.stage_status,
            outcome=session.outcome,
            pending_outcome=session.pending_outcome,
            application_id=session.application_id,
            capability_id=session.capability_id,
            capability_version=session.capability_version,
            payload_summary=payload,
            redacted_fields=redacted_fields,
            previous_event_hash=previous_hash,
            event_hash=event_hash,
            occurred_at=occurred_at,
        )

    def verify_chain(self, events: list[AuditEvent]) -> bool:
        previous_hash = None
        for event in events:
            if event.previous_event_hash != previous_hash:
                return False
            expected = self._digest(
                self._hash_input(
                    sequence_number=event.sequence_number,
                    session_id=event.session_id,
                    conversation_id=event.conversation_id,
                    action_event_id=event.action_event_id,
                    user_id=event.user_id,
                    actor_id=event.actor_id,
                    event_source=event.event_source,
                    event_type=event.event_type,
                    trace_id=event.trace_id,
                    correlation_id=event.correlation_id,
                    causation_id=event.causation_id,
                    source_stage=event.source_stage,
                    source_status=event.source_status,
                    target_stage=event.target_stage,
                    target_status=event.target_status,
                    outcome=event.outcome.value,
                    pending_outcome=(
                        event.pending_outcome.value if event.pending_outcome else None
                    ),
                    application_id=event.application_id,
                    capability_id=event.capability_id,
                    capability_version=event.capability_version,
                    payload_summary=dict(event.payload_summary),
                    redacted_fields=event.redacted_fields,
                    previous_event_hash=event.previous_event_hash,
                    occurred_at=event.occurred_at,
                )
            )
            if event.event_hash != expected:
                return False
            previous_hash = event.event_hash
        return True

    @staticmethod
    def _optional_text(values: dict[str, Any], field_name: str | None) -> str | None:
        if field_name is None:
            return None
        value = values.get(field_name)
        return None if value is None else str(value)

    @staticmethod
    def _hash_input(**values: Any) -> dict[str, Any]:
        values["source_stage"] = values["source_stage"].value
        values["source_status"] = values["source_status"].value
        values["target_stage"] = values["target_stage"].value
        values["target_status"] = values["target_status"].value
        for name in ("session_id", "conversation_id", "action_event_id", "causation_id"):
            values[name] = str(values[name]) if values[name] is not None else None
        values["redacted_fields"] = list(values["redacted_fields"])
        values["occurred_at"] = values["occurred_at"].isoformat()
        return values

    @staticmethod
    def _digest(values: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


__all__ = ["AuditTrailService"]
