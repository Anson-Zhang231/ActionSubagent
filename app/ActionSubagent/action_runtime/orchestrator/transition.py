from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from action_runtime.domain.action_session import (
    ActionOutcome,
    ActionStage,
    StageStatus,
    WaitingReason,
)
from action_runtime.orchestrator.events import ActionEventType

TransitionKey = tuple[ActionStage, StageStatus, ActionEventType]


@dataclass(frozen=True, slots=True)
class TransitionRule:
    """
    Declarative definition of one allowed state transition.

    target_stage=None means that the current stage is retained.

    finalize_outcome=True means that pending_outcome is copied into
    outcome. This is permitted only after AUDIT_WRITTEN.
    """

    target_stage: ActionStage | None
    target_status: StageStatus

    waiting_reason: WaitingReason | None = None
    pending_outcome: ActionOutcome | None = None

    finalize_outcome: bool = False
    clear_error: bool = True

    description: str = ""


class IllegalTransitionError(Exception):
    def __init__(self, *, stage: ActionStage, status: StageStatus, event_type: ActionEventType):
        self.stage = stage
        self.status = status
        self.event_type = event_type

        super().__init__(
            f"Illegal state transition: {stage.value} + {status.value} + {event_type.value}"
        )


class TransitionRegistryBuilder:
    """
    Builds the transition registry and prevents duplicate rules.
    """

    def __init__(self) -> None:
        self._rules: dict[TransitionKey, TransitionRule] = {}

    def add(
        self,
        *,
        source_stage: ActionStage,
        source_status: StageStatus,
        event_type: ActionEventType,
        target_stage: ActionStage | None,
        target_status: StageStatus,
        waiting_reason: WaitingReason | None = None,
        pending_outcome: ActionOutcome | None = None,
        finalize_outcome: bool = False,
        clear_error: bool = True,
        description: str = "",
    ) -> None:
        key = (source_stage, source_status, event_type)
        effective_target_stage = target_stage or source_stage

        if finalize_outcome and pending_outcome is not None:
            raise ValueError("A finalizing transition cannot assign pending_outcome")

        if target_status is StageStatus.SUCCEEDED:
            if effective_target_stage is not ActionStage.AUDIT:
                raise ValueError("Only AUDIT can enter SUCCEEDED")
            if not finalize_outcome:
                raise ValueError("AUDIT SUCCEEDED transition must finalize outcome")

        if key in self._rules:
            raise ValueError(
                "Duplicate transition rule: "
                f"{source_stage.value} + "
                f"{source_status.value} + "
                f"{event_type.value}"
            )
        if target_status is StageStatus.WAITING and waiting_reason is None:
            raise ValueError("WAITING transition requires waiting_reason")

        if target_status is not StageStatus.WAITING and waiting_reason is not None:
            raise ValueError("waiting_reason is allowed only for WAITING")

        if pending_outcome is ActionOutcome.PENDING:
            raise ValueError("pending_outcome cannot be PENDING")

        if finalize_outcome:
            if source_stage is not ActionStage.AUDIT:
                raise ValueError("Only AUDIT can finalize outcome")

            if event_type is not ActionEventType.AUDIT_WRITTEN:
                raise ValueError("Only AUDIT_WRITTEN can finalize outcome")

            if target_status is not StageStatus.SUCCEEDED:
                raise ValueError("Finalization requires SUCCEEDED status")

        self._rules[key] = TransitionRule(
            target_stage=target_stage,
            target_status=target_status,
            waiting_reason=waiting_reason,
            pending_outcome=pending_outcome,
            finalize_outcome=finalize_outcome,
            clear_error=clear_error,
            description=description,
        )

    def contains(self, key: TransitionKey) -> bool:
        return key in self._rules

    def build(self) -> Mapping[TransitionKey, TransitionRule]:
        return MappingProxyType(dict(self._rules))


builder = TransitionRegistryBuilder()

# --- Understand ---

builder.add(
    source_stage=ActionStage.UNDERSTAND,
    source_status=StageStatus.READY,
    event_type=ActionEventType.UNDERSTANDING_STARTED,
    target_stage=None,
    target_status=StageStatus.RUNNING,
    description="Begin understanding the user request.",
)

builder.add(
    source_stage=ActionStage.UNDERSTAND,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.INTENT_UNDERSTOOD,
    target_stage=ActionStage.DISCOVER_CAPABILITY,
    target_status=StageStatus.READY,
    description="A valid structured intent was produced.",
)

builder.add(
    source_stage=ActionStage.UNDERSTAND,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.INTENT_CLARIFICATION_REQUIRED,
    target_stage=None,
    target_status=StageStatus.WAITING,
    waiting_reason=WaitingReason.USER_CLARIFICATION,
    description="Wait for the user to clarify the intent.",
)

builder.add(
    source_stage=ActionStage.UNDERSTAND,
    source_status=StageStatus.WAITING,
    event_type=ActionEventType.USER_CLARIFICATION_RECEIVED,
    target_stage=None,
    target_status=StageStatus.READY,
    description="Restart understanding with the clarification.",
)

builder.add(
    source_stage=ActionStage.UNDERSTAND,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.UNDERSTANDING_FAILED,
    target_stage=None,
    target_status=StageStatus.RETRYABLE_FAILED,
    clear_error=False,
    description="Understanding failed temporarily.",
)

# --- Discover Capability ---

builder.add(
    source_stage=ActionStage.DISCOVER_CAPABILITY,
    source_status=StageStatus.READY,
    event_type=ActionEventType.CAPABILITY_DISCOVERY_STARTED,
    target_stage=None,
    target_status=StageStatus.RUNNING,
    description="Begin capability discovery.",
)

builder.add(
    source_stage=ActionStage.DISCOVER_CAPABILITY,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.CAPABILITY_SELECTED,
    target_stage=ActionStage.COLLECT_DATA,
    target_status=StageStatus.READY,
    description="Proceed with the registry-validated capability.",
)

builder.add(
    source_stage=ActionStage.DISCOVER_CAPABILITY,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.CAPABILITY_SELECTION_REQUIRED,
    target_stage=None,
    target_status=StageStatus.WAITING,
    waiting_reason=WaitingReason.CAPABILITY_SELECTION,
    description="Wait for the user to select a candidate.",
)

builder.add(
    source_stage=ActionStage.DISCOVER_CAPABILITY,
    source_status=StageStatus.WAITING,
    event_type=ActionEventType.USER_CAPABILITY_SELECTED,
    target_stage=None,
    target_status=StageStatus.READY,
    description="Resume capability discovery to validate the user-selected capability.",
)

builder.add(
    source_stage=ActionStage.DISCOVER_CAPABILITY,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.CAPABILITY_NOT_FOUND,
    target_stage=ActionStage.UNDERSTAND,
    target_status=StageStatus.WAITING,
    waiting_reason=WaitingReason.USER_CLARIFICATION,
    description="Return to Understand because no capability matched.",
)

builder.add(
    source_stage=ActionStage.DISCOVER_CAPABILITY,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.CAPABILITY_DISCOVERY_FAILED,
    target_stage=None,
    target_status=StageStatus.RETRYABLE_FAILED,
    clear_error=False,
    description="Capability discovery failed temporarily.",
)

# --- Collect Data ---

builder.add(
    source_stage=ActionStage.COLLECT_DATA,
    source_status=StageStatus.READY,
    event_type=ActionEventType.DATA_COLLECTION_STARTED,
    target_stage=None,
    target_status=StageStatus.RUNNING,
    description="Begin extracting and collecting parameters.",
)

builder.add(
    source_stage=ActionStage.COLLECT_DATA,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.REQUIRED_DATA_MISSING,
    target_stage=None,
    target_status=StageStatus.WAITING,
    waiting_reason=WaitingReason.REQUIRED_PARAMETERS,
    description="Wait for missing required parameters.",
)

builder.add(
    source_stage=ActionStage.COLLECT_DATA,
    source_status=StageStatus.WAITING,
    event_type=ActionEventType.PARAMETER_DATA_RECEIVED,
    target_stage=None,
    target_status=StageStatus.READY,
    description="Reprocess parameters supplied by the user.",
)

builder.add(
    source_stage=ActionStage.COLLECT_DATA,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.REQUIRED_DATA_COMPLETE,
    target_stage=ActionStage.VALIDATE,
    target_status=StageStatus.READY,
    description="All required candidate parameters are present.",
)

builder.add(
    source_stage=ActionStage.COLLECT_DATA,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.DATA_COLLECTION_FAILED,
    target_stage=None,
    target_status=StageStatus.RETRYABLE_FAILED,
    clear_error=False,
    description="Parameter collection failed temporarily.",
)

# --- Validate ---

builder.add(
    source_stage=ActionStage.VALIDATE,
    source_status=StageStatus.READY,
    event_type=ActionEventType.VALIDATION_STARTED,
    target_stage=None,
    target_status=StageStatus.RUNNING,
    description="Begin deterministic parameter validation.",
)

builder.add(
    source_stage=ActionStage.VALIDATE,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.VALIDATION_PASSED,
    target_stage=ActionStage.AUTHORIZE,
    target_status=StageStatus.READY,
    description="All parameter validation rules passed.",
)

builder.add(
    source_stage=ActionStage.VALIDATE,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.VALIDATION_FAILED,
    target_stage=ActionStage.COLLECT_DATA,
    target_status=StageStatus.WAITING,
    waiting_reason=WaitingReason.PARAMETER_CORRECTION,
    clear_error=False,
    description="Return invalid parameters to the user.",
)

builder.add(
    source_stage=ActionStage.VALIDATE,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.VALIDATION_SERVICE_FAILED,
    target_stage=None,
    target_status=StageStatus.RETRYABLE_FAILED,
    clear_error=False,
    description="The validation component failed temporarily.",
)

# --- Authorize ---

builder.add(
    source_stage=ActionStage.AUTHORIZE,
    source_status=StageStatus.READY,
    event_type=ActionEventType.AUTHORIZATION_STARTED,
    target_stage=None,
    target_status=StageStatus.RUNNING,
    description="Begin deterministic authorization.",
)

builder.add(
    source_stage=ActionStage.AUTHORIZE,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.AUTHORIZATION_GRANTED,
    target_stage=ActionStage.CONFIRM,
    target_status=StageStatus.READY,
    description="The policy service granted permission.",
)

builder.add(
    source_stage=ActionStage.AUTHORIZE,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.AUTHORIZATION_DENIED,
    target_stage=ActionStage.AUDIT,
    target_status=StageStatus.READY,
    pending_outcome=ActionOutcome.DENIED,
    clear_error=False,
    description="Authorization was denied; proceed to audit.",
)

builder.add(
    source_stage=ActionStage.AUTHORIZE,
    source_status=StageStatus.RUNNING,
    event_type=(ActionEventType.AUTHORIZATION_SERVICE_FAILED),
    target_stage=None,
    target_status=StageStatus.RETRYABLE_FAILED,
    clear_error=False,
    description="The policy service failed temporarily.",
)

# --- Confirm ---

builder.add(
    source_stage=ActionStage.CONFIRM,
    source_status=StageStatus.READY,
    event_type=ActionEventType.CONFIRMATION_REQUESTED,
    target_stage=None,
    target_status=StageStatus.WAITING,
    waiting_reason=WaitingReason.USER_CONFIRMATION,
    description="Wait for explicit user confirmation.",
)

builder.add(
    source_stage=ActionStage.CONFIRM,
    source_status=StageStatus.READY,
    event_type=ActionEventType.CONFIRMATION_SKIPPED,
    target_stage=ActionStage.EXECUTE,
    target_status=StageStatus.READY,
    description="Proceed directly to execution when confirmation is not required.",
)

builder.add(
    source_stage=ActionStage.CONFIRM,
    source_status=StageStatus.WAITING,
    event_type=ActionEventType.ACTION_CONFIRMED,
    target_stage=ActionStage.EXECUTE,
    target_status=StageStatus.READY,
    description="The user confirmed the exact action payload.",
)

builder.add(
    source_stage=ActionStage.CONFIRM,
    source_status=StageStatus.WAITING,
    event_type=ActionEventType.ACTION_MODIFICATION_REQUESTED,
    target_stage=ActionStage.COLLECT_DATA,
    target_status=StageStatus.WAITING,
    waiting_reason=WaitingReason.PARAMETER_CORRECTION,
    description="Return to data collection and invalidate confirmation.",
)

builder.add(
    source_stage=ActionStage.CONFIRM,
    source_status=StageStatus.WAITING,
    event_type=ActionEventType.ACTION_CANCELLED,
    target_stage=ActionStage.AUDIT,
    target_status=StageStatus.READY,
    pending_outcome=ActionOutcome.CANCELLED,
    description="The user cancelled the action.",
)

builder.add(
    source_stage=ActionStage.CONFIRM,
    source_status=StageStatus.WAITING,
    event_type=ActionEventType.CONFIRMATION_EXPIRED,
    target_stage=ActionStage.AUDIT,
    target_status=StageStatus.READY,
    pending_outcome=ActionOutcome.TIMED_OUT,
    description="The confirmation window expired.",
)

builder.add(
    source_stage=ActionStage.CONFIRM,
    source_status=StageStatus.WAITING,
    event_type=ActionEventType.CONFIRMATION_FAILED,
    target_stage=None,
    target_status=StageStatus.RETRYABLE_FAILED,
    clear_error=False,
    description="Confirmation processing failed temporarily.",
)

builder.add(
    source_stage=ActionStage.EXECUTE,
    source_status=StageStatus.READY,
    event_type=ActionEventType.AUTHORIZATION_REVOKED,
    target_stage=ActionStage.AUDIT,
    target_status=StageStatus.READY,
    pending_outcome=ActionOutcome.DENIED,
    clear_error=False,
    description="Authorization was revoked before unconfirmed execution.",
)

# --- Execute ---

builder.add(
    source_stage=ActionStage.EXECUTE,
    source_status=StageStatus.READY,
    event_type=ActionEventType.EXECUTION_STARTED,
    target_stage=None,
    target_status=StageStatus.RUNNING,
    description="Begin execution through a registered connector.",
)

builder.add(
    source_stage=ActionStage.EXECUTE,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.EXECUTION_RETURNED,
    target_stage=ActionStage.VERIFY,
    target_status=StageStatus.READY,
    description="The target returned a result; verification is required.",
)

builder.add(
    source_stage=ActionStage.EXECUTE,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.EXECUTION_FAILED,
    target_stage=ActionStage.AUDIT,
    target_status=StageStatus.READY,
    pending_outcome=ActionOutcome.FAILED,
    clear_error=False,
    description="Execution produced a definite failure.",
)

builder.add(
    source_stage=ActionStage.EXECUTE,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.EXECUTION_TIMED_OUT,
    target_stage=ActionStage.VERIFY,
    target_status=StageStatus.READY,
    description=(
        "Execution timed out; verification must determine whether the action was applied."
    ),
)

builder.add(
    source_stage=ActionStage.EXECUTE,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.EXECUTION_STATUS_UNKNOWN,
    target_stage=ActionStage.VERIFY,
    target_status=StageStatus.READY,
    description="Execution status is unknown; verification is mandatory.",
)

# --- Verify ---

builder.add(
    source_stage=ActionStage.VERIFY,
    source_status=StageStatus.READY,
    event_type=ActionEventType.VERIFICATION_STARTED,
    target_stage=None,
    target_status=StageStatus.RUNNING,
    description="Begin business-result verification.",
)

builder.add(
    source_stage=ActionStage.VERIFY,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.VERIFICATION_PASSED,
    target_stage=ActionStage.AUDIT,
    target_status=StageStatus.READY,
    pending_outcome=ActionOutcome.COMPLETED,
    description="The business result was verified successfully.",
)

builder.add(
    source_stage=ActionStage.VERIFY,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.VERIFICATION_FAILED,
    target_stage=ActionStage.AUDIT,
    target_status=StageStatus.READY,
    pending_outcome=ActionOutcome.FAILED,
    clear_error=False,
    description="The business result was verified as failed.",
)

builder.add(
    source_stage=ActionStage.VERIFY,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.VERIFICATION_INDETERMINATE,
    target_stage=ActionStage.AUDIT,
    target_status=StageStatus.READY,
    pending_outcome=ActionOutcome.INDETERMINATE,
    clear_error=False,
    description="The final business result remains unknown.",
)

builder.add(
    source_stage=ActionStage.VERIFY,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.VERIFICATION_SERVICE_FAILED,
    target_stage=None,
    target_status=StageStatus.RETRYABLE_FAILED,
    clear_error=False,
    description="The verification component failed temporarily.",
)

# --- Audit ---

builder.add(
    source_stage=ActionStage.AUDIT,
    source_status=StageStatus.READY,
    event_type=ActionEventType.AUDIT_STARTED,
    target_stage=None,
    target_status=StageStatus.RUNNING,
    description="Begin final audit persistence.",
)

builder.add(
    source_stage=ActionStage.AUDIT,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.AUDIT_WRITTEN,
    target_stage=None,
    target_status=StageStatus.SUCCEEDED,
    finalize_outcome=True,
    description="Audit completed; finalize pending_outcome.",
)

builder.add(
    source_stage=ActionStage.AUDIT,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.AUDIT_WRITE_FAILED,
    target_stage=None,
    target_status=StageStatus.RETRYABLE_FAILED,
    clear_error=False,
    description="Audit failed; the session must remain non-terminal.",
)

# --- Unhandled Failure ---

builder.add(
    source_stage=ActionStage.EXECUTE,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.UNHANDLED_FAILURE_DETECTED,
    target_stage=ActionStage.VERIFY,
    target_status=StageStatus.READY,
    clear_error=False,
    description="Execution may have reached the target system; verification is mandatory.",
)

builder.add(
    source_stage=ActionStage.VERIFY,
    source_status=StageStatus.RUNNING,
    event_type=ActionEventType.UNHANDLED_FAILURE_DETECTED,
    target_stage=ActionStage.AUDIT,
    target_status=StageStatus.READY,
    pending_outcome=ActionOutcome.INDETERMINATE,
    clear_error=False,
    description="Verification stopped unexpectedly; the business result is indeterminate.",
)

# --- Retry Transitions ---

RETRYABLE_STAGES: Final[tuple[ActionStage, ...]] = (
    ActionStage.UNDERSTAND,
    ActionStage.DISCOVER_CAPABILITY,
    ActionStage.COLLECT_DATA,
    ActionStage.VALIDATE,
    ActionStage.AUTHORIZE,
    ActionStage.CONFIRM,
    ActionStage.VERIFY,
    ActionStage.AUDIT,
)

for retryable_stage in RETRYABLE_STAGES:
    builder.add(
        source_stage=retryable_stage,
        source_status=StageStatus.RETRYABLE_FAILED,
        event_type=ActionEventType.RETRY_REQUESTED,
        target_stage=None,
        target_status=StageStatus.READY,
        description=f"Retry the {retryable_stage.value} stage.",
    )

# --- User Cancellation Before Execution Begins ---

CANCELLABLE_STATES: Final[tuple[tuple[ActionStage, StageStatus], ...]] = (
    (ActionStage.UNDERSTAND, StageStatus.READY),
    (ActionStage.UNDERSTAND, StageStatus.WAITING),
    (ActionStage.DISCOVER_CAPABILITY, StageStatus.READY),
    (ActionStage.DISCOVER_CAPABILITY, StageStatus.WAITING),
    (ActionStage.COLLECT_DATA, StageStatus.READY),
    (ActionStage.COLLECT_DATA, StageStatus.WAITING),
    (ActionStage.VALIDATE, StageStatus.READY),
    (ActionStage.AUTHORIZE, StageStatus.READY),
    (ActionStage.CONFIRM, StageStatus.READY),
    (ActionStage.CONFIRM, StageStatus.WAITING),
    (ActionStage.EXECUTE, StageStatus.READY),
)

for cancellable_stage, cancellable_status in CANCELLABLE_STATES:
    builder.add(
        source_stage=cancellable_stage,
        source_status=cancellable_status,
        event_type=ActionEventType.SESSION_CANCELLATION_REQUESTED,
        target_stage=ActionStage.AUDIT,
        target_status=StageStatus.READY,
        pending_outcome=ActionOutcome.CANCELLED,
        description="Cancel before an external execution is in progress.",
    )

# --- Session Expiration Before Execution Begins ---

EXPIRABLE_STATES: Final[tuple[tuple[ActionStage, StageStatus], ...]] = CANCELLABLE_STATES

for expirable_stage, expirable_status in EXPIRABLE_STATES:
    builder.add(
        source_stage=expirable_stage,
        source_status=expirable_status,
        event_type=ActionEventType.SESSION_EXPIRED,
        target_stage=ActionStage.AUDIT,
        target_status=StageStatus.READY,
        pending_outcome=ActionOutcome.TIMED_OUT,
        description="The inactive ActionSession expired.",
    )

# --- Unexpected Failures ---

FAILURE_SAFE_STATES: Final[tuple[tuple[ActionStage, StageStatus], ...]] = tuple(
    (stage, status)
    for stage in ActionStage
    if stage is not ActionStage.AUDIT
    for status in (
        StageStatus.READY,
        StageStatus.RUNNING,
        StageStatus.WAITING,
        StageStatus.RETRYABLE_FAILED,
    )
)

for failure_stage, failure_status in FAILURE_SAFE_STATES:
    key = (failure_stage, failure_status, ActionEventType.UNHANDLED_FAILURE_DETECTED)

    if not builder.contains(key):
        builder.add(
            source_stage=failure_stage,
            source_status=failure_status,
            event_type=ActionEventType.UNHANDLED_FAILURE_DETECTED,
            target_stage=ActionStage.AUDIT,
            target_status=StageStatus.READY,
            pending_outcome=ActionOutcome.FAILED,
            clear_error=False,
            description="Move an unhandled platform failure to Audit.",
        )

TRANSITIONS: Final[Mapping[TransitionKey, TransitionRule]] = builder.build()


def get_transition_rule(
    *,
    stage: ActionStage,
    status: StageStatus,
    event_type: ActionEventType,
) -> TransitionRule:
    """
    Returns the transition rule or rejects the event.
    """
    key: TransitionKey = (stage, status, event_type)
    rule = TRANSITIONS.get(key)

    if rule is None:
        raise IllegalTransitionError(stage=stage, status=status, event_type=event_type)

    return rule
