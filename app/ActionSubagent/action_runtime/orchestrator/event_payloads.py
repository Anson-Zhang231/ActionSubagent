from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from action_runtime.domain.action_session import ActionOutcome, ActionStage
from action_runtime.orchestrator.events import ActionEventType


class StrictPayload(BaseModel):
    """
    Base class for every event payload.

    Unknown fields are rejected so callers cannot silently inject
    unrecognized data into a state-machine event.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


class EmptyPayload(StrictPayload):
    """
    Used only when an event does not require stage-specific data.
    """

    pass


class ErrorPayload(StrictPayload):
    """
    Common structure for processing or service failures.
    """

    error_code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    error_message: str = Field(min_length=1)
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationIssuePayload(StrictPayload):
    field: str | None = None
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


# --- Session Lifecycle ---


class SessionStartedPayload(StrictPayload):
    initial_message_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)


class SessionExpiredPayload(StrictPayload):
    expired_at: datetime
    reason: str = Field(min_length=1)


class SessionCancellationRequestedPayload(StrictPayload):
    reason: str | None = None


class RetryRequestedPayload(StrictPayload):
    target_stage: ActionStage
    reason: str = Field(min_length=1)
    expected_error_code: str | None = None


class UnhandledFailureDetectedPayload(ErrorPayload):
    failed_stage: ActionStage


# --- Understand ---


class UnderstandingStartedPayload(StrictPayload):
    message_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    model_reference: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)


class IntentUnderstoodPayload(StrictPayload):
    goal: str = Field(min_length=1)
    entities: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)

    confidence: float = Field(ge=0.0, le=1.0)

    language: str = Field(min_length=1)
    model_reference: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)


class IntentClarificationRequiredPayload(StrictPayload):
    reason: str = Field(min_length=1)
    question: str = Field(min_length=1)
    missing_information: list[str] = Field(min_length=1)


class UserClarificationReceivedPayload(StrictPayload):
    message_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class UnderstandingFailedPayload(ErrorPayload):
    model_reference: str | None = None


# --- Discover Capability ---


class CapabilityDiscoveryStartedPayload(StrictPayload):
    registry_revision: str = Field(min_length=1)
    available_application_count: int = Field(ge=0)
    available_capability_count: int = Field(ge=0)
    attempt: int = Field(ge=1)


class CapabilitySelectedPayload(StrictPayload):
    application_id: str = Field(min_length=1)
    capability_id: str = Field(min_length=1)
    capability_version: str = Field(min_length=1)

    match_score: float = Field(ge=0.0, le=1.0)

    selection_method: Literal["MODEL_MATCH", "RULE_MATCH", "USER_SELECTION_VALIDATED"]

    registry_revision: str = Field(min_length=1)
    definition_hash: str = Field(min_length=1)


class CapabilityNotFoundPayload(StrictPayload):
    registry_revision: str = Field(min_length=1)
    searched_capability_ids: list[str]
    reason: str = Field(min_length=1)
    clarification_question: str = Field(min_length=1)


class CapabilityCandidatePayload(StrictPayload):
    application_id: str = Field(min_length=1)
    capability_id: str = Field(min_length=1)
    capability_version: str = Field(min_length=1)
    display_name: str = Field(min_length=1)

    match_score: float = Field(ge=0.0, le=1.0)


class CapabilitySelectionRequiredPayload(StrictPayload):
    candidates: list[CapabilityCandidatePayload] = Field(min_length=2)

    question: str = Field(min_length=1)
    registry_revision: str = Field(min_length=1)


class UserCapabilitySelectedPayload(StrictPayload):
    """
    The user supplies only the selected ID.

    Version, definition hash, application ID, and Registry revision
    must be reloaded and verified by the Registry.
    """

    capability_id: str = Field(min_length=1)


class CapabilityDiscoveryFailedPayload(ErrorPayload):
    registry_revision: str | None = None


#  --- Collect Data ---


class DataCollectionStartedPayload(StrictPayload):
    capability_id: str = Field(min_length=1)
    capability_version: str = Field(min_length=1)
    schema_hash: str = Field(min_length=1)
    attempt: int = Field(ge=1)


class ParameterDataReceivedPayload(StrictPayload):
    message_id: str = Field(min_length=1)

    parameters: dict[str, Any] = Field(min_length=1)

    parameter_sources: dict[str, str] = Field(min_length=1)


class RequiredDataMissingPayload(StrictPayload):
    missing_parameters: list[str] = Field(min_length=1)

    questions: dict[str, str] = Field(min_length=1)

    currently_collected_parameters: dict[str, Any]


class RequiredDataCompletePayload(StrictPayload):
    parameters: dict[str, Any]
    parameter_sources: dict[str, str]
    missing_parameters: list[str]
    schema_hash: str = Field(min_length=1)


class DataCollectionFailedPayload(ErrorPayload):
    capability_id: str = Field(min_length=1)


# --- Validate ---


class ValidationStartedPayload(StrictPayload):
    schema_hash: str = Field(min_length=1)
    validation_rule_version: str = Field(min_length=1)
    parameter_hash: str = Field(min_length=1)
    attempt: int = Field(ge=1)


class ValidationPassedPayload(StrictPayload):
    validated_parameters: dict[str, Any]
    parameter_hash: str = Field(min_length=1)
    schema_hash: str = Field(min_length=1)
    validation_rule_version: str = Field(min_length=1)
    validated_at: datetime


class ValidationFailedPayload(StrictPayload):
    issues: list[ValidationIssuePayload] = Field(min_length=1)

    rejected_parameter_hash: str = Field(min_length=1)
    schema_hash: str = Field(min_length=1)


class ValidationServiceFailedPayload(ErrorPayload):
    validation_rule_version: str | None = None


# --- Authorize ---


class AuthorizationStartedPayload(StrictPayload):
    capability_id: str = Field(min_length=1)
    capability_version: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    parameter_hash: str = Field(min_length=1)
    attempt: int = Field(ge=1)


class AuthorizationGrantedPayload(StrictPayload):
    decision_id: str = Field(min_length=1)
    decision: Literal["ALLOW"]

    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)

    capability_id: str = Field(min_length=1)
    capability_version: str = Field(min_length=1)

    granted_scopes: list[str]
    resource_scope: dict[str, Any]

    evaluated_at: datetime
    expires_at: datetime | None = None


class AuthorizationDeniedPayload(StrictPayload):
    decision_id: str = Field(min_length=1)
    decision: Literal["DENY"]

    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)

    capability_id: str = Field(min_length=1)
    capability_version: str = Field(min_length=1)

    reason_code: str = Field(min_length=1)
    reason_message: str = Field(min_length=1)

    evaluated_at: datetime


class AuthorizationRevokedPayload(StrictPayload):
    decision_id: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)


class AuthorizationServiceFailedPayload(ErrorPayload):
    policy_id: str | None = None
    policy_version: str | None = None


# --- Confirm ---


class ConfirmationRequestedPayload(StrictPayload):
    confirmation_id: str = Field(min_length=1)

    capability_id: str = Field(min_length=1)
    capability_version: str = Field(min_length=1)

    authorization_decision_id: str = Field(min_length=1)

    summary_hash: str = Field(min_length=1)
    parameter_hash: str = Field(min_length=1)

    expires_at: datetime


class ConfirmationSkippedPayload(StrictPayload):
    capability_id: str = Field(min_length=1)
    capability_version: str = Field(min_length=1)
    authorization_decision_id: str = Field(min_length=1)
    reason: Literal["NOT_REQUIRED"]


class ActionConfirmedPayload(StrictPayload):
    """
    SecretStr prevents the plain token from appearing in normal
    serialization or logging output.
    """

    confirmation_id: str = Field(min_length=1)
    confirmation_token: SecretStr

    summary_hash: str = Field(min_length=1)
    parameter_hash: str = Field(min_length=1)

    capability_version: str = Field(min_length=1)
    authorization_decision_id: str = Field(min_length=1)

    confirmed_by: str = Field(min_length=1)
    confirmed_at: datetime


class ActionModificationRequestedPayload(StrictPayload):
    fields_to_modify: list[str] = Field(min_length=1)
    reason: str | None = None


class ActionCancelledPayload(StrictPayload):
    reason: str | None = None
    cancelled_at: datetime


class ConfirmationExpiredPayload(StrictPayload):
    confirmation_id: str = Field(min_length=1)
    expired_at: datetime


class ConfirmationFailedPayload(ErrorPayload):
    confirmation_id: str | None = None


# --- Execute ---


class ExecutionStartedPayload(StrictPayload):
    execution_id: str = Field(min_length=1)
    connector_type: str = Field(min_length=1)

    application_id: str = Field(min_length=1)
    capability_id: str = Field(min_length=1)
    capability_version: str = Field(min_length=1)

    request_hash: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)

    started_at: datetime
    attempt: int = Field(ge=1)


class ExecutionReturnedPayload(StrictPayload):
    execution_id: str = Field(min_length=1)
    connector_type: str = Field(min_length=1)

    request_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)

    http_status: int = Field(ge=100, le=599)

    response: dict[str, Any]

    started_at: datetime
    finished_at: datetime


class ExecutionFailedPayload(ErrorPayload):
    execution_id: str = Field(min_length=1)
    connector_type: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)

    request_sent: bool
    failed_at: datetime


class ExecutionTimedOutPayload(StrictPayload):
    execution_id: str = Field(min_length=1)
    connector_type: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)

    request_sent: bool
    timeout_seconds: float = Field(gt=0)
    timed_out_at: datetime


class ExecutionStatusUnknownPayload(StrictPayload):
    execution_id: str = Field(min_length=1)
    connector_type: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)

    reason: str = Field(min_length=1)
    detected_at: datetime


# --- Verify ---


class VerificationStartedPayload(StrictPayload):
    execution_id: str = Field(min_length=1)
    verification_rule_version: str = Field(min_length=1)
    started_at: datetime
    attempt: int = Field(ge=1)


class VerificationPassedPayload(StrictPayload):
    execution_id: str = Field(min_length=1)
    decision: Literal["VERIFIED_SUCCESS"]

    rule_version: str = Field(min_length=1)
    evidence: dict[str, Any] = Field(min_length=1)
    normalized_result: dict[str, Any]

    verified_at: datetime


class VerificationFailedPayload(StrictPayload):
    execution_id: str = Field(min_length=1)
    decision: Literal["VERIFIED_FAILURE"]

    rule_version: str = Field(min_length=1)
    evidence: dict[str, Any] = Field(min_length=1)

    reason_code: str = Field(min_length=1)
    reason_message: str = Field(min_length=1)

    verified_at: datetime


class VerificationIndeterminatePayload(StrictPayload):
    execution_id: str = Field(min_length=1)
    decision: Literal["INDETERMINATE"]

    rule_version: str = Field(min_length=1)
    evidence: dict[str, Any] = Field(min_length=1)

    reason: str = Field(min_length=1)
    verified_at: datetime


class VerificationServiceFailedPayload(ErrorPayload):
    execution_id: str = Field(min_length=1)
    rule_version: str | None = None


# --- Audit ---


class AuditStartedPayload(StrictPayload):
    pending_outcome: ActionOutcome
    expected_event_count: int = Field(ge=1)
    started_at: datetime
    attempt: int = Field(ge=1)


class AuditWrittenPayload(StrictPayload):
    audit_record_id: str = Field(min_length=1)
    event_count: int = Field(ge=1)
    record_hash: str = Field(min_length=1)
    stored_at: datetime


class AuditWriteFailedPayload(ErrorPayload):
    attempted_event_count: int = Field(ge=1)
    failed_at: datetime


# --- Event to Payload Registry ---

EVENT_PAYLOAD_MODELS: dict[
    ActionEventType,
    type[StrictPayload],
] = {
    # Session
    ActionEventType.SESSION_STARTED: SessionStartedPayload,
    ActionEventType.SESSION_EXPIRED: SessionExpiredPayload,
    ActionEventType.SESSION_CANCELLATION_REQUESTED: SessionCancellationRequestedPayload,
    ActionEventType.RETRY_REQUESTED: RetryRequestedPayload,
    ActionEventType.UNHANDLED_FAILURE_DETECTED: UnhandledFailureDetectedPayload,
    # Understand
    ActionEventType.UNDERSTANDING_STARTED: UnderstandingStartedPayload,
    ActionEventType.INTENT_UNDERSTOOD: IntentUnderstoodPayload,
    ActionEventType.INTENT_CLARIFICATION_REQUIRED: IntentClarificationRequiredPayload,
    ActionEventType.USER_CLARIFICATION_RECEIVED: UserClarificationReceivedPayload,
    ActionEventType.UNDERSTANDING_FAILED: UnderstandingFailedPayload,
    # Discover Capability
    ActionEventType.CAPABILITY_DISCOVERY_STARTED: CapabilityDiscoveryStartedPayload,
    ActionEventType.CAPABILITY_SELECTED: CapabilitySelectedPayload,
    ActionEventType.CAPABILITY_NOT_FOUND: CapabilityNotFoundPayload,
    ActionEventType.CAPABILITY_SELECTION_REQUIRED: CapabilitySelectionRequiredPayload,
    ActionEventType.USER_CAPABILITY_SELECTED: UserCapabilitySelectedPayload,
    ActionEventType.CAPABILITY_DISCOVERY_FAILED: CapabilityDiscoveryFailedPayload,
    # Collect Data
    ActionEventType.DATA_COLLECTION_STARTED: DataCollectionStartedPayload,
    ActionEventType.PARAMETER_DATA_RECEIVED: ParameterDataReceivedPayload,
    ActionEventType.REQUIRED_DATA_MISSING: RequiredDataMissingPayload,
    ActionEventType.REQUIRED_DATA_COMPLETE: RequiredDataCompletePayload,
    ActionEventType.DATA_COLLECTION_FAILED: DataCollectionFailedPayload,
    # Validate
    ActionEventType.VALIDATION_STARTED: ValidationStartedPayload,
    ActionEventType.VALIDATION_PASSED: ValidationPassedPayload,
    ActionEventType.VALIDATION_FAILED: ValidationFailedPayload,
    ActionEventType.VALIDATION_SERVICE_FAILED: ValidationServiceFailedPayload,
    # Authorize
    ActionEventType.AUTHORIZATION_STARTED: AuthorizationStartedPayload,
    ActionEventType.AUTHORIZATION_GRANTED: AuthorizationGrantedPayload,
    ActionEventType.AUTHORIZATION_DENIED: AuthorizationDeniedPayload,
    ActionEventType.AUTHORIZATION_REVOKED: AuthorizationRevokedPayload,
    ActionEventType.AUTHORIZATION_SERVICE_FAILED: AuthorizationServiceFailedPayload,
    # Confirm
    ActionEventType.CONFIRMATION_REQUESTED: ConfirmationRequestedPayload,
    ActionEventType.CONFIRMATION_SKIPPED: ConfirmationSkippedPayload,
    ActionEventType.ACTION_CONFIRMED: ActionConfirmedPayload,
    ActionEventType.ACTION_MODIFICATION_REQUESTED: ActionModificationRequestedPayload,
    ActionEventType.ACTION_CANCELLED: ActionCancelledPayload,
    ActionEventType.CONFIRMATION_EXPIRED: ConfirmationExpiredPayload,
    ActionEventType.CONFIRMATION_FAILED: ConfirmationFailedPayload,
    # Execute
    ActionEventType.EXECUTION_STARTED: ExecutionStartedPayload,
    ActionEventType.EXECUTION_RETURNED: ExecutionReturnedPayload,
    ActionEventType.EXECUTION_FAILED: ExecutionFailedPayload,
    ActionEventType.EXECUTION_TIMED_OUT: ExecutionTimedOutPayload,
    ActionEventType.EXECUTION_STATUS_UNKNOWN: ExecutionStatusUnknownPayload,
    # Verify
    ActionEventType.VERIFICATION_STARTED: VerificationStartedPayload,
    ActionEventType.VERIFICATION_PASSED: VerificationPassedPayload,
    ActionEventType.VERIFICATION_FAILED: VerificationFailedPayload,
    ActionEventType.VERIFICATION_INDETERMINATE: VerificationIndeterminatePayload,
    ActionEventType.VERIFICATION_SERVICE_FAILED: VerificationServiceFailedPayload,
    # Audit
    ActionEventType.AUDIT_STARTED: AuditStartedPayload,
    ActionEventType.AUDIT_WRITTEN: AuditWrittenPayload,
    ActionEventType.AUDIT_WRITE_FAILED: AuditWriteFailedPayload,
}


def validate_payload_registry():
    """
    Fails at application startup if any event type has no Payload model.
    """
    registered = set(EVENT_PAYLOAD_MODELS)
    declared = set(ActionEventType)

    missing = declared - registered
    unknown = registered - declared

    if missing:
        names = sorted(event_type.value for event_type in missing)
        raise RuntimeError("Event Payload models are missing for: " + ", ".join(names))

    if unknown:
        names = sorted(event_type.value for event_type in unknown)
        raise RuntimeError("Payload registry contains unknown events: " + ", ".join(names))


def validate_event_payload(*, event_type: ActionEventType, payload: dict[str, Any]):
    """
    Validates and returns the typed Payload for an event.
    """
    payload_model = EVENT_PAYLOAD_MODELS.get(event_type)

    if payload_model is None:
        raise ValueError(f"No Payload model registered for event type {event_type.value}")

    return payload_model.model_validate(payload)


validate_payload_registry()
