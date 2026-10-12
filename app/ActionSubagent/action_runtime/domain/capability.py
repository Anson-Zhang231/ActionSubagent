from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from action_runtime.domain.master_data import MasterDataValidationRule

IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
SEMANTIC_VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


class CapabilityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class PermissionMatchMode(StrEnum):
    ALL = "ALL"
    ANY = "ANY"


class ConnectorType(StrEnum):
    HTTP = "HTTP"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class MappingSourceType(StrEnum):
    PARAMETER = "PARAMETER"
    RESPONSE = "RESPONSE"
    CONSTANT = "CONSTANT"
    APPLICATION_USER_ID = "APPLICATION_USER_ID"


class MappingTargetType(StrEnum):
    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    OBJECT = "OBJECT"
    ARRAY = "ARRAY"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class BusinessRuleType(StrEnum):
    FIELD_COMPARISON = "FIELD_COMPARISON"


class ComparisonOperator(StrEnum):
    LESS_THAN = "LESS_THAN"
    LESS_THAN_OR_EQUAL = "LESS_THAN_OR_EQUAL"
    GREATER_THAN = "GREATER_THAN"
    GREATER_THAN_OR_EQUAL = "GREATER_THAN_OR_EQUAL"
    EQUAL = "EQUAL"
    NOT_EQUAL = "NOT_EQUAL"


@dataclass(frozen=True, slots=True)
class PermissionDefinition:
    """
    Permissions required to use a Capability.

    Permission IDs are resolved by the authorization component later.
    """

    required_permissions: tuple[str, ...]
    match_mode: PermissionMatchMode = PermissionMatchMode.ALL

    def __post_init__(self) -> None:
        if not self.required_permissions:
            raise ValueError("required_permissions must not be empty")

        if any(
            not IDENTIFIER_PATTERN.fullmatch(permission) for permission in self.required_permissions
        ):
            raise ValueError("permission ids must use the standard identifier format")

        if len(self.required_permissions) != len(set(self.required_permissions)):
            raise ValueError("required_permissions must not contain duplicates")


@dataclass(frozen=True, slots=True)
class ConfirmationDefinition:
    """
    Defines whether confirmation is required and, when it is, what is shown.
    """

    required: bool
    risk_level: RiskLevel | None
    message_template: str | None

    def __post_init__(self) -> None:
        if self.required:
            if self.risk_level is None:
                raise ValueError("risk_level is required when confirmation is required")
            if not self.message_template or not self.message_template.strip():
                raise ValueError("message_template is required when confirmation is required")
        elif self.risk_level is not None or self.message_template is not None:
            raise ValueError(
                "risk_level and message_template must be null when confirmation is not required"
            )


@dataclass(frozen=True, slots=True)
class RetryPolicyDefinition:
    """Declarative limits for retrying one external Action execution."""

    max_attempts: int = 1
    backoff_seconds: float = 0.0
    retry_on_timeout: bool = False
    retry_on_transport_error: bool = False
    retryable_http_statuses: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise ValueError("max_attempts must be an integer")
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        if self.backoff_seconds < 0 or self.backoff_seconds > 30:
            raise ValueError("backoff_seconds must be between 0 and 30")
        if not isinstance(self.retry_on_timeout, bool):
            raise ValueError("retry_on_timeout must be a boolean")
        if not isinstance(self.retry_on_transport_error, bool):
            raise ValueError("retry_on_transport_error must be a boolean")
        if any(status < 400 or status > 599 for status in self.retryable_http_statuses):
            raise ValueError("retryable HTTP statuses must be between 400 and 599")
        if len(self.retryable_http_statuses) != len(set(self.retryable_http_statuses)):
            raise ValueError("retryable_http_statuses must not contain duplicates")


@dataclass(frozen=True, slots=True)
class FieldMappingDefinition:
    """One declarative source-to-target field transformation."""

    source_type: MappingSourceType
    source: Any
    required: bool = True
    target_type: MappingTargetType | None = None
    default_value: Any = None
    has_default: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.required, bool):
            raise ValueError("mapping required must be a boolean")
        if self.source_type in {MappingSourceType.PARAMETER, MappingSourceType.RESPONSE} and (
            not isinstance(self.source, str) or not self.source.strip()
        ):
            raise ValueError("parameter and response mapping sources must be non-empty paths")
        if self.source_type is MappingSourceType.APPLICATION_USER_ID and self.source != "user_id":
            raise ValueError("APPLICATION_USER_ID mapping source must be 'user_id'")
        if self.source_type is MappingSourceType.CONSTANT and self.has_default:
            raise ValueError("constant mappings must not define a default")


@dataclass(frozen=True, slots=True)
class ResponseHandlingDefinition:
    """Defines how a standardized result is identified and explained to the user."""

    transaction_id_field: str | None
    status_field: str | None
    success_message_template: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("transaction_id_field", self.transaction_id_field),
            ("status_field", self.status_field),
        ):
            if value is not None and not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} must use the standard identifier format")
        if not self.success_message_template.strip():
            raise ValueError("success_message_template must not be empty")


@dataclass(frozen=True, slots=True)
class HttpExecutionDefinition:
    """
    Declarative HTTP execution contract.

    The Application supplies the base URL. This definition supplies
    only a relative path and parameter mappings.
    """

    connector: ConnectorType
    method: HttpMethod
    path: str

    path_parameters: Mapping[str, FieldMappingDefinition] = field(default_factory=dict)
    query_parameters: Mapping[str, FieldMappingDefinition] = field(default_factory=dict)
    body_parameters: Mapping[str, FieldMappingDefinition] = field(default_factory=dict)
    response_mapping: Mapping[str, FieldMappingDefinition] = field(default_factory=dict)

    timeout_seconds: float = 30.0
    idempotency_required: bool = False
    send_body_on_get: bool = False
    retry_policy: RetryPolicyDefinition = field(default_factory=RetryPolicyDefinition)

    def __post_init__(self) -> None:
        if self.connector is not ConnectorType.HTTP:
            raise ValueError("HttpExecutionDefinition requires HTTP connector")

        if not isinstance(self.idempotency_required, bool):
            raise ValueError("idempotency_required must be a boolean")

        if not self.path.startswith("/"):
            raise ValueError("execution path must be relative and start with '/'")

        if "://" in self.path:
            raise ValueError("execution path must not contain a base URL")

        if "?" in self.path or "#" in self.path:
            raise ValueError("execution path must not contain query or fragment")

        if not isinstance(self.send_body_on_get, bool):
            raise ValueError("send_body_on_get must be a boolean")

        if self.send_body_on_get and self.method is not HttpMethod.GET:
            raise ValueError("send_body_on_get is only valid for GET execution")

        if self.method is HttpMethod.DELETE and self.body_parameters:
            raise ValueError(f"{self.method.value} execution must not define body parameters")

        if self.method is HttpMethod.GET and self.body_parameters and not self.send_body_on_get:
            raise ValueError("GET execution with body parameters requires send_body_on_get")

        if self.method is HttpMethod.GET and self.idempotency_required:
            raise ValueError("GET execution must not require idempotency")

        if (
            self.retry_policy.max_attempts > 1
            and self.method is not HttpMethod.GET
            and not self.idempotency_required
        ):
            raise ValueError("retrying a mutating execution requires idempotency")

        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        mappings = (
            tuple(self.path_parameters.values())
            + tuple(self.query_parameters.values())
            + tuple(self.body_parameters.values())
        )
        parameter_names = [
            mapping.source
            for mapping in mappings
            if mapping.source_type is MappingSourceType.PARAMETER
        ]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("an input parameter cannot be mapped to multiple request locations")
        if any(
            mapping.source_type
            not in {
                MappingSourceType.PARAMETER,
                MappingSourceType.CONSTANT,
                MappingSourceType.APPLICATION_USER_ID,
            }
            for mapping in mappings
        ):
            raise ValueError("request mappings may only use PARAMETER or CONSTANT sources")
        if any(not mapping.required for mapping in self.path_parameters.values()):
            raise ValueError("path mappings must be required")
        if any(
            mapping.source_type not in {MappingSourceType.RESPONSE, MappingSourceType.CONSTANT}
            for mapping in self.response_mapping.values()
        ):
            raise ValueError("response mappings may only use RESPONSE or CONSTANT sources")


@dataclass(frozen=True, slots=True)
class VerificationDefinition:
    accepted_http_statuses: tuple[int, ...]
    required_response_fields: tuple[str, ...] = ()
    expected_field_values: Mapping[str, Any] = field(default_factory=dict)
    status_check_capability_id: str | None = None

    def __post_init__(self) -> None:
        if not self.accepted_http_statuses:
            raise ValueError("accepted_http_statuses must not be empty")

        if any(status < 100 or status > 599 for status in self.accepted_http_statuses):
            raise ValueError("HTTP status values must be between 100 and 599")

        if len(set(self.accepted_http_statuses)) != len(self.accepted_http_statuses):
            raise ValueError("accepted_http_statuses must not contain duplicates")

        if any(
            not isinstance(field_name, str) or not field_name.strip()
            for field_name in self.required_response_fields
        ):
            raise ValueError("required response fields must be non-empty strings")

        if len(set(self.required_response_fields)) != len(self.required_response_fields):
            raise ValueError("required_response_fields must not contain duplicates")

        if not isinstance(self.expected_field_values, Mapping):
            raise ValueError("expected_field_values must be a mapping")

        if any(
            not isinstance(field_name, str) or not field_name.strip()
            for field_name in self.expected_field_values
        ):
            raise ValueError("expected field names must be non-empty strings")

        unknown_expected_fields = set(self.expected_field_values) - set(
            self.required_response_fields
        )

        if unknown_expected_fields:
            raise ValueError(
                "expected_field_values must reference "
                "required_response_fields: "
                f"{sorted(unknown_expected_fields)}"
            )

        if self.status_check_capability_id is not None and not IDENTIFIER_PATTERN.fullmatch(
            self.status_check_capability_id
        ):
            raise ValueError("status_check_capability_id must use the standard identifier format")


@dataclass(frozen=True, slots=True)
class ApprovalDefinition:
    """Organizational approval required after user confirmation."""

    required: bool
    policy_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.required, bool):
            raise ValueError("approval required must be a boolean")

        if self.policy_id is not None and not isinstance(self.policy_id, str):
            raise ValueError("approval policy_id must be a string or null")

        if self.required and not self.policy_id:
            raise ValueError("policy_id is required when additional approval is required")

        if not self.required and self.policy_id is not None:
            raise ValueError("policy_id must be omitted when additional approval is not required")

        if self.policy_id is not None and not IDENTIFIER_PATTERN.fullmatch(self.policy_id):
            raise ValueError("policy_id must use the standard identifier format")


@dataclass(frozen=True, slots=True)
class BusinessRuleDefinition:
    id: str
    type: BusinessRuleType
    left_field: str
    operator: ComparisonOperator
    right_field: str
    target_field: str
    message: str

    def __post_init__(self) -> None:
        if not IDENTIFIER_PATTERN.fullmatch(self.id):
            raise ValueError("business rule id must use the standard identifier format")

        for field_name, value in (
            ("left_field", self.left_field),
            ("right_field", self.right_field),
            ("target_field", self.target_field),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(
                    f"business rule {field_name} must use the standard identifier format"
                )

        if not self.message.strip():
            raise ValueError("business rule message must not be empty")


@dataclass(frozen=True, slots=True)
class Capability:
    """
    Versioned definition of one Action exposed by an Application.

    The Orchestrator must consume this definition dynamically and
    must not contain Application-specific behavior.
    """

    id: str
    application_id: str
    version: str
    api_version: str
    input_schema_version: str
    output_schema_version: str

    name: str
    description: str
    status: CapabilityStatus

    discovery_intents: tuple[str, ...]
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    business_rules: tuple[BusinessRuleDefinition, ...]
    master_data_rules: tuple[MasterDataValidationRule, ...]

    permissions: PermissionDefinition
    confirmation: ConfirmationDefinition
    approval: ApprovalDefinition
    execution: HttpExecutionDefinition
    verification: VerificationDefinition
    response_handling: ResponseHandlingDefinition

    sensitive_fields: tuple[str, ...]

    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for field_name, value in (
            ("id", self.id),
            ("application_id", self.application_id),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} must use the standard identifier format")

        for field_name, value in (
            ("version", self.version),
            ("api_version", self.api_version),
            ("input_schema_version", self.input_schema_version),
            ("output_schema_version", self.output_schema_version),
        ):
            if not SEMANTIC_VERSION_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} must use semantic versioning")

        for field_name, value in (
            ("name", self.name),
            ("description", self.description),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty")

        if not self.discovery_intents:
            raise ValueError("discovery_intents must not be empty")

        if any(not intent.strip() for intent in self.discovery_intents):
            raise ValueError("discovery intents must not contain empty values")

        if len(set(self.discovery_intents)) != len(self.discovery_intents):
            raise ValueError("discovery_intents must not contain duplicates")

        if self.input_schema.get("type") != "object":
            raise ValueError("input_schema root type must be object")

        properties = self.input_schema.get("properties")
        if not isinstance(properties, Mapping):
            raise ValueError("input_schema must define properties")

        output_properties = self.output_schema.get("properties")
        if not isinstance(output_properties, Mapping):
            raise ValueError("output_schema must define properties")

        required = self.input_schema.get("required", [])

        rule_ids = [rule.id for rule in self.business_rules]

        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("business rule ids must not contain duplicates")

        for rule in self.business_rules:
            referenced_fields = {
                rule.left_field,
                rule.right_field,
                rule.target_field,
            }
            unknown_fields = referenced_fields - set(properties)

            if unknown_fields:
                raise ValueError(
                    "business rules reference unknown input properties: "
                    + ", ".join(sorted(unknown_fields))
                )

        master_data_fields = [rule.field for rule in self.master_data_rules]
        if len(master_data_fields) != len(set(master_data_fields)):
            raise ValueError("master data rules must not contain duplicate fields")
        unknown_master_data_fields = set(master_data_fields) - set(properties)
        if unknown_master_data_fields:
            raise ValueError(
                "master data rules reference unknown input properties: "
                + ", ".join(sorted(unknown_master_data_fields))
            )

        if not isinstance(required, list):
            raise ValueError("input_schema required must be a list")

        unknown_required = set(required) - set(properties)

        if unknown_required:
            raise ValueError(
                "required parameters are missing from properties: "
                + ", ".join(sorted(unknown_required))
            )

        if any(field_name not in properties for field_name in self.sensitive_fields):
            raise ValueError("sensitive_fields must reference input properties")

        if len(set(self.sensitive_fields)) != len(self.sensitive_fields):
            raise ValueError("sensitive_fields must not contain duplicates")

        for field_name, value in (
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")

        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be before created_at")

    @property
    def is_active(self) -> bool:
        return self.status is CapabilityStatus.ACTIVE
