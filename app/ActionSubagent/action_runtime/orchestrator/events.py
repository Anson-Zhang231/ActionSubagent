from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4


class EventSource(StrEnum):
    """
    Identifies the trusted source that produced an event.

    The source is used for authorization, auditing, and protection
    against users forging internal state-machine events.
    """

    USER = "USER"
    CHAT_PLATFORM = "CHAT_PLATFORM"
    ORCHESTRATOR = "ORCHESTRATOR"
    LLM = "LLM"
    REGISTRY = "REGISTRY"
    POLICY_SERVICE = "POLICY_SERVICE"
    CONNECTOR = "CONNECTOR"
    VERIFICATION_ENGINE = "VERIFICATION_ENGINE"
    AUDIT_SERVICE = "AUDIT_SERVICE"
    SYSTEM = "SYSTEM"


class ActionEventType(StrEnum):
    """
    Events that may affect an ActionSession.

    Events describe facts that have already happened. They do not
    directly decide the next stage.
    """

    # Session lifecycle
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    SESSION_CANCELLATION_REQUESTED = "SESSION_CANCELLATION_REQUESTED"
    RETRY_REQUESTED = "RETRY_REQUESTED"
    UNHANDLED_FAILURE_DETECTED = "UNHANDLED_FAILURE_DETECTED"

    # 1. Understand
    UNDERSTANDING_STARTED = "UNDERSTANDING_STARTED"
    INTENT_UNDERSTOOD = "INTENT_UNDERSTOOD"
    INTENT_CLARIFICATION_REQUIRED = "INTENT_CLARIFICATION_REQUIRED"
    USER_CLARIFICATION_RECEIVED = "USER_CLARIFICATION_RECEIVED"
    UNDERSTANDING_FAILED = "UNDERSTANDING_FAILED"

    # 2. Discover Capability
    CAPABILITY_DISCOVERY_STARTED = "CAPABILITY_DISCOVERY_STARTED"
    CAPABILITY_SELECTED = "CAPABILITY_SELECTED"
    CAPABILITY_NOT_FOUND = "CAPABILITY_NOT_FOUND"
    CAPABILITY_SELECTION_REQUIRED = "CAPABILITY_SELECTION_REQUIRED"
    USER_CAPABILITY_SELECTED = "USER_CAPABILITY_SELECTED"
    CAPABILITY_DISCOVERY_FAILED = "CAPABILITY_DISCOVERY_FAILED"

    # 3. Collect Data
    DATA_COLLECTION_STARTED = "DATA_COLLECTION_STARTED"
    PARAMETER_DATA_RECEIVED = "PARAMETER_DATA_RECEIVED"
    REQUIRED_DATA_MISSING = "REQUIRED_DATA_MISSING"
    REQUIRED_DATA_COMPLETE = "REQUIRED_DATA_COMPLETE"
    DATA_COLLECTION_FAILED = "DATA_COLLECTION_FAILED"

    # 4. Validate
    VALIDATION_STARTED = "VALIDATION_STARTED"
    VALIDATION_PASSED = "VALIDATION_PASSED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATION_SERVICE_FAILED = "VALIDATION_SERVICE_FAILED"

    # 5. Authorize
    AUTHORIZATION_STARTED = "AUTHORIZATION_STARTED"
    AUTHORIZATION_GRANTED = "AUTHORIZATION_GRANTED"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    AUTHORIZATION_REVOKED = "AUTHORIZATION_REVOKED"
    AUTHORIZATION_SERVICE_FAILED = "AUTHORIZATION_SERVICE_FAILED"

    # 6. Confirm
    CONFIRMATION_REQUESTED = "CONFIRMATION_REQUESTED"
    CONFIRMATION_SKIPPED = "CONFIRMATION_SKIPPED"
    ACTION_CONFIRMED = "ACTION_CONFIRMED"
    ACTION_MODIFICATION_REQUESTED = "ACTION_MODIFICATION_REQUESTED"
    ACTION_CANCELLED = "ACTION_CANCELLED"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    CONFIRMATION_FAILED = "CONFIRMATION_FAILED"

    # 7. Execute
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_RETURNED = "EXECUTION_RETURNED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_TIMED_OUT = "EXECUTION_TIMED_OUT"
    EXECUTION_STATUS_UNKNOWN = "EXECUTION_STATUS_UNKNOWN"

    # 8. Verify
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_PASSED = "VERIFICATION_PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFICATION_INDETERMINATE = "VERIFICATION_INDETERMINATE"
    VERIFICATION_SERVICE_FAILED = "VERIFICATION_SERVICE_FAILED"

    # 9. Audit
    AUDIT_STARTED = "AUDIT_STARTED"
    AUDIT_WRITTEN = "AUDIT_WRITTEN"
    AUDIT_WRITE_FAILED = "AUDIT_WRITE_FAILED"


@dataclass(frozen=True, slots=True)
class ActionEvent:
    """
    Immutable event delivered to the state machine.

    expected_session_version provides optimistic concurrency control.
    correlation_id connects all events belonging to one business flow.
    causation_id identifies the event that caused this event.
    """

    id: UUID
    session_id: UUID
    event_type: ActionEventType
    source: EventSource

    actor_id: str
    payload: Mapping[str, Any]

    expected_session_version: int

    correlation_id: str
    causation_id: UUID | None

    occurred_at: datetime

    @classmethod
    def create(
        cls,
        *,
        session_id: UUID,
        event_type: ActionEventType,
        source: EventSource,
        actor_id: str,
        expected_session_version: int,
        correlation_id: str,
        payload: Mapping[str, Any] | None = None,
        causation_id: UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> ActionEvent:
        """
        Creates and validates an immutable state-machine event.
        """

        event = cls(
            id=uuid4(),
            session_id=session_id,
            event_type=event_type,
            source=source,
            actor_id=actor_id,
            payload=MappingProxyType(dict(payload or {})),
            expected_session_version=expected_session_version,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at or datetime.now(UTC),
        )

        event.validate()
        return event

    def validate(self) -> None:
        if not self.actor_id.strip():
            raise ValueError("actor_id must not be empty")

        if not self.correlation_id.strip():
            raise ValueError("correlation_id must not be empty")

        if self.expected_session_version < 1:
            raise ValueError("expected_session_version must be at least 1")

        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")


USER_EVENT_TYPES = frozenset(
    {
        ActionEventType.USER_CLARIFICATION_RECEIVED,
        ActionEventType.USER_CAPABILITY_SELECTED,
        ActionEventType.PARAMETER_DATA_RECEIVED,
        ActionEventType.ACTION_CONFIRMED,
        ActionEventType.ACTION_MODIFICATION_REQUESTED,
        ActionEventType.ACTION_CANCELLED,
        ActionEventType.SESSION_CANCELLATION_REQUESTED,
        ActionEventType.RETRY_REQUESTED,
    }
)

INTERNAL_EVENT_TYPES = frozenset(
    event_type for event_type in ActionEventType if event_type not in USER_EVENT_TYPES
)


def validate_event_source(event: ActionEvent) -> None:
    """
    Rejects events whose declared source is not permitted.

    This is only the first source-validation layer. More specific
    source rules are applied by the transition guards.
    """
    if event.event_type in USER_EVENT_TYPES and event.source not in {
        EventSource.USER,
        EventSource.CHAT_PLATFORM,
    }:
        raise ValueError(f"{event.event_type.value} must originate from USER or CHAT_PLATFORM")

    if event.event_type in INTERNAL_EVENT_TYPES and event.source in {
        EventSource.USER,
        EventSource.CHAT_PLATFORM,
    }:
        raise ValueError(f"{event.event_type.value} cannot originate from USER or CHAT_PLATFORM")
