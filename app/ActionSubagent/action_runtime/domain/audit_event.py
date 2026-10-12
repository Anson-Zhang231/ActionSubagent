from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from action_runtime.domain.action_session import ActionOutcome, ActionStage, StageStatus
from action_runtime.domain.identity import validate_identifier, validate_text


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """
    Immutable append-only record of one ActionSession state transition.

    Audit payloads preserve original values in the action summary.
    """

    id: UUID
    sequence_number: int

    session_id: UUID
    conversation_id: UUID
    action_event_id: UUID

    user_id: str
    actor_id: str
    event_source: str
    event_type: str

    trace_id: str
    correlation_id: str
    causation_id: UUID | None

    source_stage: ActionStage
    source_status: StageStatus
    target_stage: ActionStage
    target_status: StageStatus

    outcome: ActionOutcome
    pending_outcome: ActionOutcome | None

    application_id: str | None
    capability_id: str | None
    capability_version: str | None

    payload_summary: Mapping[str, Any] = field(default_factory=dict)
    redacted_fields: tuple[str, ...] = ()

    previous_event_hash: str | None = None
    event_hash: str = ""

    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload_summary", MappingProxyType(dict(self.payload_summary)))
        self.validate_invariants()

    @classmethod
    def create(
        cls,
        *,
        sequence_number: int,
        session_id: UUID,
        conversation_id: UUID,
        action_event_id: UUID,
        user_id: str,
        actor_id: str,
        event_source: str,
        event_type: str,
        trace_id: str,
        correlation_id: str,
        causation_id: UUID | None,
        source_stage: ActionStage,
        source_status: StageStatus,
        target_stage: ActionStage,
        target_status: StageStatus,
        outcome: ActionOutcome,
        pending_outcome: ActionOutcome | None,
        application_id: str | None,
        capability_id: str | None,
        capability_version: str | None,
        payload_summary: Mapping[str, Any],
        redacted_fields: tuple[str, ...],
        previous_event_hash: str | None,
        event_hash: str,
        occurred_at: datetime,
    ) -> AuditEvent:
        return cls(
            id=uuid4(),
            sequence_number=sequence_number,
            session_id=session_id,
            conversation_id=conversation_id,
            action_event_id=action_event_id,
            user_id=user_id,
            actor_id=actor_id,
            event_source=event_source,
            event_type=event_type,
            trace_id=trace_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            source_stage=source_stage,
            source_status=source_status,
            target_stage=target_stage,
            target_status=target_status,
            outcome=outcome,
            pending_outcome=pending_outcome,
            application_id=application_id,
            capability_id=capability_id,
            capability_version=capability_version,
            payload_summary=payload_summary,
            redacted_fields=redacted_fields,
            previous_event_hash=previous_event_hash,
            event_hash=event_hash,
            occurred_at=occurred_at,
            recorded_at=datetime.now(UTC),
        )

    def validate_invariants(self) -> None:
        if self.sequence_number < 1:
            raise ValueError("sequence_number must be greater than or equal to 1")

        validate_text(field_name="user_id", value=self.user_id)

        for field_name, value in (
            ("actor_id", self.actor_id),
            ("event_source", self.event_source),
            ("event_type", self.event_type),
            ("trace_id", self.trace_id),
            ("correlation_id", self.correlation_id),
            ("event_hash", self.event_hash),
        ):
            validate_text(field_name=field_name, value=value)

        capability_reference = (self.application_id, self.capability_id, self.capability_version)

        if any(value is not None for value in capability_reference) and any(
            value is None for value in capability_reference
        ):
            raise ValueError(
                "application_id, capability_id, and capability_version must be assigned together"
            )

        if self.application_id is not None:
            validate_identifier(field_name="application_id", value=self.application_id)
            validate_identifier(field_name="capability_id", value=self.capability_id)
            validate_text(field_name="capability_version", value=self.capability_version)

        if self.pending_outcome is ActionOutcome.PENDING:
            raise ValueError("pending_outcome cannot be PENDING")

        if self.outcome is not ActionOutcome.PENDING and self.pending_outcome is not None:
            raise ValueError("pending_outcome must be empty after outcome is finalized")

        if self.outcome is not ActionOutcome.PENDING:
            if self.target_stage is not ActionStage.AUDIT:
                raise ValueError("a final outcome is allowed only at the AUDIT stage")

            if self.target_status is not StageStatus.SUCCEEDED:
                raise ValueError("a final outcome requires a successful AUDIT stage")

        if len(self.redacted_fields) != len(set(self.redacted_fields)):
            raise ValueError("redacted_fields must not contain duplicates")

        if any(not field_name.strip() for field_name in self.redacted_fields):
            raise ValueError("redacted_fields must not contain empty values")

        if self.previous_event_hash is not None:
            validate_text(field_name="previous_event_hash", value=self.previous_event_hash)

        for field_name, value in (
            ("occurred_at", self.occurred_at),
            ("recorded_at", self.recorded_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")

        if self.recorded_at < self.occurred_at:
            raise ValueError("recorded_at must not be before occurred_at")
