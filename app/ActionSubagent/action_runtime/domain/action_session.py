from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class ActionStage(StrEnum):
    """
    The mandatory stages of every action.

    The order is controlled by the state machine. Declaring the enum
    does not allow callers to move between stages directly.
    """

    UNDERSTAND = "UNDERSTAND"
    DISCOVER_CAPABILITY = "DISCOVER_CAPABILITY"
    COLLECT_DATA = "COLLECT_DATA"
    VALIDATE = "VALIDATE"
    AUTHORIZE = "AUTHORIZE"
    CONFIRM = "CONFIRM"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    AUDIT = "AUDIT"


class StageStatus(StrEnum):
    """
    Runtime status of the current stage.
    """

    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    FAILED = "FAILED"


class ActionOutcome(StrEnum):
    """
    Final business outcome of an ActionSession.

    The outcome must remain PENDING until the AUDIT stage succeeds.
    """

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    DENIED = "DENIED"
    INDETERMINATE = "INDETERMINATE"


class WaitingReason(StrEnum):
    """
    Explains why the current stage is waiting.

    This avoids creating many additional top-level states such as
    WAITING_FOR_TITLE or WAITING_FOR_CONFIRMATION.
    """

    USER_CLARIFICATION = "USER_CLARIFICATION"
    CAPABILITY_SELECTION = "CAPABILITY_SELECTION"
    REQUIRED_PARAMETERS = "REQUIRED_PARAMETERS"
    PARAMETER_CORRECTION = "PARAMETER_CORRECTION"
    USER_CONFIRMATION = "USER_CONFIRMATION"
    EXTERNAL_SYSTEM = "EXTERNAL_SYSTEM"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"


@dataclass(frozen=True, slots=True)
class ActionError:
    """
    Standard error stored against the ActionSession.
    """

    code: str
    message: str
    stage: ActionStage
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.code):
            raise ValueError("error code must use the standard identifier format")

        if not self.message.strip():
            raise ValueError("error message must not be empty")

        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("error occurred_at must be timezone-aware")


@dataclass(slots=True)
class ActionSession:
    """
    Aggregate representing one complete action lifecycle.

    Only the state machine service is allowed to change:
    - stage
    - stage_status
    - outcome
    - pending_outcome
    - waiting_reason
    - last_error
    - version
    """

    id: UUID
    conversation_id: UUID
    user_id: str

    stage: ActionStage
    stage_status: StageStatus

    outcome: ActionOutcome
    pending_outcome: ActionOutcome | None

    waiting_reason: WaitingReason | None
    last_error: ActionError | None

    trace_id: str
    version: int

    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    application_id: str | None = None
    capability_id: str | None = None
    capability_version: str | None = None
    api_version: str | None = None
    input_schema_version: str | None = None
    output_schema_version: str | None = None
    output_schema_hash: str | None = None

    collected_parameters: dict[str, Any] = field(default_factory=dict)
    parameter_schema_hash: str | None = None
    validated_parameter_hash: str | None = None

    authorization_decision_id: str | None = None
    confirmation_id: UUID | None = None
    execution_id: UUID | None = None

    @classmethod
    def start(
        cls,
        *,
        conversation_id: UUID,
        user_id: str,
        trace_id: str,
        expires_at: datetime,
    ) -> ActionSession:
        """
        Creates a new session at the first mandatory stage.
        """
        now = datetime.now(UTC)

        session = cls(
            id=uuid4(),
            conversation_id=conversation_id,
            user_id=user_id,
            stage=ActionStage.UNDERSTAND,
            stage_status=StageStatus.READY,
            outcome=ActionOutcome.PENDING,
            pending_outcome=None,
            waiting_reason=None,
            last_error=None,
            trace_id=trace_id,
            version=1,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            application_id=None,
            capability_id=None,
            capability_version=None,
            api_version=None,
            input_schema_version=None,
            output_schema_version=None,
            output_schema_hash=None,
            collected_parameters={},
            parameter_schema_hash=None,
            validated_parameter_hash=None,
            authorization_decision_id=None,
            confirmation_id=None,
            execution_id=None,
        )

        session.validate_invariants()
        return session

    @property
    def is_terminal(self) -> bool:
        """
        A session is terminal only after a final outcome has been assigned.
        """
        return self.outcome is not ActionOutcome.PENDING

    @property
    def is_waiting(self) -> bool:
        return self.stage_status is StageStatus.WAITING

    @property
    def can_accept_event(self) -> bool:
        """
        Terminal sessions cannot accept normal business events.
        """
        return not self.is_terminal

    def validate_invariants(self) -> None:
        """
        Validates rules that must always hold for ActionSession.

        This method does not perform transitions. It only detects an
        invalid state created by programming or persistence errors.
        """
        if not self.user_id.strip():
            raise ValueError("user_id must not be empty")

        if not self.trace_id.strip():
            raise ValueError("trace_id must not be empty")

        if self.version < 1:
            raise ValueError("version must be greater than or equal to 1")

        if self.stage_status is StageStatus.WAITING and self.waiting_reason is None:
            raise ValueError("waiting_reason is required when stage_status is WAITING")

        if self.stage_status is not StageStatus.WAITING and self.waiting_reason is not None:
            raise ValueError("waiting_reason must be empty unless stage_status is WAITING")

        if self.outcome is not ActionOutcome.PENDING and self.pending_outcome is not None:
            raise ValueError("pending_outcome must be empty after outcome is finalized")

        for field_name, value in (
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
            ("expires_at", self.expires_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")

        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be before created_at")

        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")

        capability_reference = (
            self.application_id,
            self.capability_id,
            self.capability_version,
            self.api_version,
            self.input_schema_version,
            self.output_schema_version,
            self.output_schema_hash,
        )

        if any(value is not None for value in capability_reference) and any(
            value is None for value in capability_reference
        ):
            raise ValueError(
                "Capability, API, and Schema version metadata must be assigned together"
            )

        for field_name, value in (
            ("application_id", self.application_id),
            ("capability_id", self.capability_id),
            ("capability_version", self.capability_version),
            ("api_version", self.api_version),
            ("input_schema_version", self.input_schema_version),
            ("output_schema_version", self.output_schema_version),
            ("output_schema_hash", self.output_schema_hash),
            ("parameter_schema_hash", self.parameter_schema_hash),
            ("validated_parameter_hash", self.validated_parameter_hash),
            ("authorization_decision_id", self.authorization_decision_id),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{field_name} must not be empty")

        if self.parameter_schema_hash is not None and self.capability_id is None:
            raise ValueError("parameter_schema_hash requires a selected capability")

        if self.output_schema_hash is not None and self.capability_id is None:
            raise ValueError("output_schema_hash requires a selected capability")

        if self.collected_parameters and self.capability_id is None:
            raise ValueError("collected_parameters require a selected capability")

        if self.validated_parameter_hash is not None and self.parameter_schema_hash is None:
            raise ValueError("validated_parameter_hash requires parameter_schema_hash")

        if self.authorization_decision_id is not None and self.validated_parameter_hash is None:
            raise ValueError("authorization_decision_id requires validated parameters")

        if self.confirmation_id is not None and self.authorization_decision_id is None:
            raise ValueError("confirmation_id requires an authorization decision")

        if self.execution_id is not None and self.authorization_decision_id is None:
            raise ValueError("execution_id requires an authorization decision")

        if self.outcome is not ActionOutcome.PENDING:
            if self.stage is not ActionStage.AUDIT:
                raise ValueError("a final outcome is allowed only at the AUDIT stage")
            if self.stage_status is not StageStatus.SUCCEEDED:
                raise ValueError("a final outcome requires a successful AUDIT stage")

        if self.stage is not ActionStage.AUDIT and self.outcome is not ActionOutcome.PENDING:
            raise ValueError("outcome must remain PENDING before the AUDIT stage")

        if self.pending_outcome is ActionOutcome.PENDING:
            raise ValueError("pending_outcome cannot be PENDING")

        if self.is_terminal and self.last_error is not None:
            if self.last_error.stage is ActionStage.AUDIT:
                raise ValueError("a session cannot be terminal when AUDIT has failed")

        return None
