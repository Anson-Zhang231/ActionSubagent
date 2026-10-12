from __future__ import annotations

import hashlib
import json
import re
import string
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from action_runtime.domain.capability import (
    ApprovalDefinition,
    BusinessRuleDefinition,
    BusinessRuleType,
    Capability,
    CapabilityStatus,
    ComparisonOperator,
    ConfirmationDefinition,
    ConnectorType,
    FieldMappingDefinition,
    HttpExecutionDefinition,
    HttpMethod,
    MappingSourceType,
    MappingTargetType,
    PermissionDefinition,
    PermissionMatchMode,
    ResponseHandlingDefinition,
    RetryPolicyDefinition,
    RiskLevel,
    VerificationDefinition,
)
from action_runtime.domain.master_data import MasterDataValidationRule


class CapabilityRegistryError(ValueError):
    """Raised when a Capability directory contains an invalid definition."""


class CapabilityNotFoundError(LookupError):
    """Raised when an active Capability version cannot be resolved."""


@dataclass(frozen=True, slots=True)
class RegisteredCapability:
    capability: Capability
    definition: dict[str, Any]
    definition_hash: str


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_schema(filename: str) -> dict[str, Any]:
    schema_path = Path(__file__).with_name("schemas") / filename

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CapabilityRegistryError(f"unable to load Schema {schema_path}: {error}") from error

    if not isinstance(schema, dict):
        raise CapabilityRegistryError(f"Schema must be a JSON object: {schema_path}")

    return schema


_INPUT_PARAMETERS_SCHEMA = _load_schema("input-parameters.schema.json")
_OUTPUT_PARAMETERS_SCHEMA = _load_schema("output-parameters.schema.json")
_CAPABILITY_SCHEMA = _load_schema("capability.schema.json")

Draft202012Validator.check_schema(_INPUT_PARAMETERS_SCHEMA)
Draft202012Validator.check_schema(_OUTPUT_PARAMETERS_SCHEMA)
Draft202012Validator.check_schema(_CAPABILITY_SCHEMA)

_SCHEMA_REGISTRY = (
    Registry()
    .with_resource(
        _INPUT_PARAMETERS_SCHEMA["$id"],
        Resource.from_contents(_INPUT_PARAMETERS_SCHEMA),
    )
    .with_resource(
        _OUTPUT_PARAMETERS_SCHEMA["$id"],
        Resource.from_contents(_OUTPUT_PARAMETERS_SCHEMA),
    )
)

_FORMAT_CHECKER = FormatChecker()

_CAPABILITY_VALIDATOR = Draft202012Validator(
    _CAPABILITY_SCHEMA,
    registry=_SCHEMA_REGISTRY,
    format_checker=_FORMAT_CHECKER,
)


class CapabilityRegistry:
    def __init__(self, entries: list[RegisteredCapability]) -> None:
        if not entries:
            raise CapabilityRegistryError("at least one Capability definition is required")

        indexed: dict[tuple[str, str, str], RegisteredCapability] = {}
        for entry in entries:
            capability = entry.capability
            if capability.is_active and capability.approval.required:
                raise CapabilityRegistryError(
                    "approval-required Capabilities cannot run until organizational approval "
                    "is implemented: "
                    f"{capability.application_id}/{capability.id}/{capability.version}"
                )
            key = (capability.application_id, capability.id, capability.version)
            if key in indexed:
                raise CapabilityRegistryError("duplicate Capability identity: " + "/".join(key))
            indexed[key] = entry

        _validate_status_check_references(indexed)
        _validate_version_compatibility(indexed)
        self._entries = indexed
        self.revision = canonical_hash(
            [
                {"identity": list(key), "definition_hash": entry.definition_hash}
                for key, entry in sorted(indexed.items())
            ]
        )

    @classmethod
    def from_directory(cls, directory: Path) -> CapabilityRegistry:
        return cls.from_directories([directory])

    @classmethod
    def from_directories(cls, directories: list[Path]) -> CapabilityRegistry:
        entries: list[RegisteredCapability] = []
        for directory in directories:
            if not directory.is_dir():
                raise CapabilityRegistryError(f"Capability directory does not exist: {directory}")

            for path in sorted(directory.rglob("*.json")):
                try:
                    definition = json.loads(path.read_text(encoding="utf-8"))
                    _validate_capability_schema(definition)
                    capability = _parse_capability(definition)
                except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                    raise CapabilityRegistryError(f"invalid Capability {path}: {error}") from error
                entries.append(
                    RegisteredCapability(
                        capability=capability,
                        definition=definition,
                        definition_hash=canonical_hash(definition),
                    )
                )
        return cls(entries)

    def list_active(self, application_id: str | None = None) -> list[RegisteredCapability]:
        return [
            entry
            for _, entry in sorted(self._entries.items())
            if entry.capability.is_active
            and (application_id is None or entry.capability.application_id == application_id)
        ]

    def list_latest_active(
        self,
        application_id: str | None = None,
    ) -> list[RegisteredCapability]:
        latest: dict[tuple[str, str], RegisteredCapability] = {}
        for entry in self.list_active(application_id):
            key = (entry.capability.application_id, entry.capability.id)
            current = latest.get(key)
            if current is None or _version_key(entry.capability.version) > _version_key(
                current.capability.version
            ):
                latest[key] = entry
        return [latest[key] for key in sorted(latest)]

    def resolve(
        self,
        *,
        application_id: str,
        capability_id: str,
        version: str | None = None,
    ) -> RegisteredCapability:
        candidates = [
            entry
            for (entry_application, entry_capability, entry_version), entry in self._entries.items()
            if entry_application == application_id
            and entry_capability == capability_id
            and (version is None or entry_version == version)
            and entry.capability.is_active
        ]
        if not candidates:
            reference = f"{application_id}/{capability_id}"
            if version is not None:
                reference += f"/{version}"
            raise CapabilityNotFoundError(reference)
        return max(candidates, key=lambda item: _version_key(item.capability.version))


def _validate_capability_schema(
    definition: Any,
) -> None:
    errors = sorted(
        _CAPABILITY_VALIDATOR.iter_errors(definition),
        key=lambda error: tuple(str(segment) for segment in error.absolute_path),
    )

    if not errors:
        return

    error = errors[0]
    location = ".".join(str(segment) for segment in error.absolute_path)

    if not location:
        location = "$"

    raise ValueError(f"schema validation failed at {location}: {error.message}")


def _validate_status_check_references(
    entries: dict[
        tuple[str, str, str],
        RegisteredCapability,
    ],
) -> None:
    active_capabilities = {
        (
            entry.capability.application_id,
            entry.capability.id,
        )
        for entry in entries.values()
        if entry.capability.is_active
    }

    for entry in entries.values():
        capability = entry.capability
        target_id = capability.verification.status_check_capability_id

        if target_id is None:
            continue

        if target_id == capability.id:
            raise CapabilityRegistryError(
                "status check Capability cannot reference itself: "
                f"{capability.application_id}/{capability.id}"
            )

        target = (
            capability.application_id,
            target_id,
        )

        if target not in active_capabilities:
            raise CapabilityRegistryError(
                "status check Capability does not exist or is inactive: "
                f"{capability.application_id}/{target_id}; "
                f"referenced by "
                f"{capability.application_id}/{capability.id}"
            )


def _validate_version_compatibility(
    entries: dict[tuple[str, str, str], RegisteredCapability],
) -> None:
    families: dict[tuple[str, str], list[RegisteredCapability]] = {}
    for entry in entries.values():
        key = (entry.capability.application_id, entry.capability.id)
        families.setdefault(key, []).append(entry)

    for (application_id, capability_id), family in families.items():
        ordered = sorted(family, key=lambda item: _version_key(item.capability.version))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            old = previous.capability
            new = current.capability
            reference = f"{application_id}/{capability_id}/{new.version}"
            for field_name in (
                "api_version",
                "input_schema_version",
                "output_schema_version",
            ):
                if _version_key(getattr(new, field_name)) < _version_key(getattr(old, field_name)):
                    raise CapabilityRegistryError(f"{reference} decreases {field_name}")

            if _version_key(old.version)[0] != _version_key(new.version)[0]:
                continue

            old_execution = old.execution
            new_execution = new.execution
            if (
                old_execution.connector != new_execution.connector
                or old_execution.method != new_execution.method
                or old_execution.path != new_execution.path
            ):
                raise CapabilityRegistryError(
                    f"{reference} changes the API contract without a Capability major version bump"
                )
            if (
                old.permissions != new.permissions
                or old.confirmation != new.confirmation
                or old.approval != new.approval
            ):
                raise CapabilityRegistryError(
                    f"{reference} changes authorization or confirmation policy without a "
                    "Capability major version bump"
                )
            for mapping_name in (
                "path_parameters",
                "query_parameters",
                "body_parameters",
                "response_mapping",
            ):
                _validate_mapping_compatibility(
                    previous=getattr(old_execution, mapping_name),
                    current=getattr(new_execution, mapping_name),
                    mapping_name=mapping_name,
                    reference=reference,
                )
            old_verification = old.verification
            new_verification = new.verification
            if (
                set(old_verification.accepted_http_statuses)
                - set(new_verification.accepted_http_statuses)
                or set(new_verification.required_response_fields)
                - set(old_verification.required_response_fields)
                or old_verification.expected_field_values != new_verification.expected_field_values
                or old_verification.status_check_capability_id
                != new_verification.status_check_capability_id
            ):
                raise CapabilityRegistryError(
                    f"{reference} tightens verification without a Capability major version bump"
                )
            if (
                old.response_handling.transaction_id_field
                != new.response_handling.transaction_id_field
                or old.response_handling.status_field != new.response_handling.status_field
            ):
                raise CapabilityRegistryError(
                    f"{reference} changes response identity fields without a Capability major "
                    "version bump"
                )
            _validate_schema_compatibility(
                previous=old.input_schema,
                current=new.input_schema,
                schema_name="input",
                reference=reference,
            )
            _validate_schema_compatibility(
                previous=old.output_schema,
                current=new.output_schema,
                schema_name="output",
                reference=reference,
            )


def _validate_schema_compatibility(
    *,
    previous: Any,
    current: Any,
    schema_name: str,
    reference: str,
) -> None:
    if schema_name == "input" and previous.get("allOf", []) != current.get("allOf", []):
        raise CapabilityRegistryError(
            f"{reference} changes conditional input requirements without a major version bump"
        )

    old_properties = previous["properties"]
    new_properties = current["properties"]
    removed = set(old_properties) - set(new_properties)
    if removed:
        raise CapabilityRegistryError(
            f"{reference} removes {schema_name} properties without a major version bump: "
            f"{sorted(removed)}"
        )

    old_required = set(previous.get("required", []))
    new_required = set(current.get("required", []))
    incompatible_required = (
        new_required - old_required if schema_name == "input" else old_required - new_required
    )
    if incompatible_required:
        direction = "adds required" if schema_name == "input" else "makes optional"
        raise CapabilityRegistryError(
            f"{reference} {direction} {schema_name} properties without a major version bump: "
            f"{sorted(incompatible_required)}"
        )

    for field_name in set(old_properties) & set(new_properties):
        old_property = old_properties[field_name]
        new_property = new_properties[field_name]
        if old_property.get("type") != new_property.get("type"):
            raise CapabilityRegistryError(
                f"{reference} changes {schema_name} property type without a major version bump: "
                f"{field_name}"
            )
        old_enum = set(old_property.get("enum", []))
        new_enum = set(new_property.get("enum", []))
        if not old_enum or not new_enum:
            continue
        incompatible_enum = old_enum - new_enum if schema_name == "input" else new_enum - old_enum
        if incompatible_enum:
            raise CapabilityRegistryError(
                f"{reference} narrows compatibility for {schema_name} property {field_name}"
            )


def _validate_mapping_compatibility(
    *,
    previous: Any,
    current: Any,
    mapping_name: str,
    reference: str,
) -> None:
    removed = set(previous) - set(current)
    changed = {
        field_name
        for field_name in set(previous) & set(current)
        if previous[field_name] != current[field_name]
    }
    if removed or changed:
        raise CapabilityRegistryError(
            f"{reference} changes existing {mapping_name} without a Capability major version "
            f"bump: removed={sorted(removed)}, changed={sorted(changed)}"
        )


def _version_key(version: str) -> tuple[int, int, int, int, str]:
    core_and_suffix = version.split("+", 1)[0]
    core, separator, prerelease = core_and_suffix.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    return major, minor, patch, 1 if not separator else 0, prerelease


def _parse_capability(definition: dict[str, Any]) -> Capability:
    expected_fields = {
        "id",
        "application_id",
        "version",
        "api_version",
        "input_schema_version",
        "output_schema_version",
        "name",
        "description",
        "status",
        "discovery_intents",
        "input_schema",
        "output_schema",
        "permissions",
        "confirmation",
        "approval",
        "execution",
        "verification",
        "response_handling",
        "sensitive_fields",
        "created_at",
        "updated_at",
        "business_rules",
        "master_data_rules",
    }
    actual_fields = set(definition)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        extra = sorted(actual_fields - expected_fields)
        raise ValueError(f"root fields mismatch; missing={missing}, extra={extra}")

    permissions = definition["permissions"]
    confirmation = definition["confirmation"]
    approval = definition["approval"]
    execution = definition["execution"]
    verification = definition["verification"]
    response_handling = definition["response_handling"]
    input_schema = definition["input_schema"]
    output_schema = definition["output_schema"]
    business_rules = definition["business_rules"]
    master_data_rules = definition["master_data_rules"]
    _validate_input_schema(input_schema)
    _validate_input_schema(output_schema)
    _validate_capability_references(
        input_schema=input_schema,
        output_schema=output_schema,
        confirmation=confirmation,
        execution=execution,
        verification=verification,
        response_handling=response_handling,
    )

    return Capability(
        id=definition["id"],
        application_id=definition["application_id"],
        version=definition["version"],
        api_version=definition["api_version"],
        input_schema_version=definition["input_schema_version"],
        output_schema_version=definition["output_schema_version"],
        name=definition["name"],
        description=definition["description"],
        status=CapabilityStatus(definition["status"]),
        discovery_intents=tuple(definition["discovery_intents"]),
        input_schema=input_schema,
        output_schema=output_schema,
        business_rules=tuple(
            BusinessRuleDefinition(
                id=rule["id"],
                type=BusinessRuleType(rule["type"]),
                left_field=rule["left_field"],
                operator=ComparisonOperator(rule["operator"]),
                right_field=rule["right_field"],
                target_field=rule["target_field"],
                message=rule["message"],
            )
            for rule in business_rules
        ),
        master_data_rules=tuple(
            MasterDataValidationRule(
                field=rule["field"],
                source_id=rule["source_id"],
                require_active=rule["require_active"],
                message=rule["message"],
            )
            for rule in master_data_rules
        ),
        permissions=PermissionDefinition(
            required_permissions=tuple(permissions["required_permissions"]),
            match_mode=PermissionMatchMode(permissions["match_mode"]),
        ),
        confirmation=ConfirmationDefinition(
            required=confirmation["required"],
            risk_level=(
                RiskLevel(confirmation["risk_level"])
                if confirmation["risk_level"] is not None
                else None
            ),
            message_template=confirmation["message_template"],
        ),
        approval=ApprovalDefinition(
            required=approval["required"],
            policy_id=approval["policy_id"],
        ),
        execution=HttpExecutionDefinition(
            connector=ConnectorType(execution["connector"]),
            method=HttpMethod(execution["method"]),
            path=execution["path"],
            path_parameters=_parse_field_mappings(execution["path_parameters"]),
            query_parameters=_parse_field_mappings(execution["query_parameters"]),
            body_parameters=_parse_field_mappings(execution["body_parameters"]),
            response_mapping=_parse_field_mappings(execution["response_mapping"]),
            timeout_seconds=execution["timeout_seconds"],
            idempotency_required=execution["idempotency_required"],
            send_body_on_get=execution.get("send_body_on_get", False),
            retry_policy=RetryPolicyDefinition(
                max_attempts=execution["retry_policy"]["max_attempts"],
                backoff_seconds=execution["retry_policy"]["backoff_seconds"],
                retry_on_timeout=execution["retry_policy"]["retry_on_timeout"],
                retry_on_transport_error=execution["retry_policy"]["retry_on_transport_error"],
                retryable_http_statuses=tuple(execution["retry_policy"]["retryable_http_statuses"]),
            ),
        ),
        verification=VerificationDefinition(
            accepted_http_statuses=tuple(verification["accepted_http_statuses"]),
            required_response_fields=tuple(verification["required_response_fields"]),
            expected_field_values=verification["expected_field_values"],
            status_check_capability_id=verification.get("status_check_capability_id"),
        ),
        response_handling=ResponseHandlingDefinition(
            transaction_id_field=response_handling["transaction_id_field"],
            status_field=response_handling["status_field"],
            success_message_template=response_handling["success_message_template"],
        ),
        sensitive_fields=tuple(definition["sensitive_fields"]),
        created_at=datetime.fromisoformat(definition["created_at"].replace("Z", "+00:00")),
        updated_at=datetime.fromisoformat(definition["updated_at"].replace("Z", "+00:00")),
    )


def _parse_field_mappings(
    mappings: dict[str, dict[str, Any]],
) -> dict[str, FieldMappingDefinition]:
    return {
        target: FieldMappingDefinition(
            source_type=MappingSourceType(definition["source_type"]),
            source=definition["source"],
            required=definition["required"],
            target_type=(
                MappingTargetType(definition["target_type"])
                if definition["target_type"] is not None
                else None
            ),
            default_value=definition.get("default"),
            has_default="default" in definition,
        )
        for target, definition in mappings.items()
    }


def _validate_input_schema(schema: dict[str, Any]) -> None:
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ValueError("input_schema must declare JSON Schema draft 2020-12")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("input_schema must be a closed object")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("input_schema properties must be an object")
    required = schema.get("required", [])
    if not isinstance(required, list) or len(required) != len(set(required)):
        raise ValueError("input_schema required must be a unique list")
    unknown = set(required) - set(properties)
    if unknown:
        raise ValueError(f"unknown required input properties: {sorted(unknown)}")
    for name, property_schema in properties.items():
        if not isinstance(name, str) or not isinstance(
            property_schema,
            dict,
        ):
            raise ValueError("input_schema properties must contain schema objects")

        _validate_property_schema(name, property_schema)

    for condition in schema.get("allOf", []):
        conditional = condition["if"]
        conditional_properties = conditional["properties"]
        condition_field = next(iter(conditional_properties))
        if conditional["required"] != [condition_field]:
            raise ValueError("conditional input required must match its condition property")
        referenced = {condition_field, *condition["then"]["required"]}
        unknown_conditional = referenced - set(properties)
        if unknown_conditional:
            raise ValueError(f"unknown conditional input properties: {sorted(unknown_conditional)}")
        allowed_values = properties[condition_field].get("enum")
        if allowed_values is not None:
            for value in conditional_properties[condition_field]["enum"]:
                if value not in allowed_values:
                    raise ValueError(
                        f"conditional value for input property {condition_field} is not in its enum"
                    )


def _validate_constraint_range(
    path: str,
    property_schema: dict[str, Any],
    *,
    minimum_key: str,
    maximum_key: str,
) -> None:
    minimum = property_schema.get(minimum_key)
    maximum = property_schema.get(maximum_key)

    if minimum is None or maximum is None:
        return

    if minimum > maximum:
        raise ValueError(
            f"invalid constraints for input property {path}: "
            f"{minimum_key} must not be greater than "
            f"{maximum_key}"
        )


def _validate_property_schema(
    path: str,
    property_schema: dict[str, Any],
) -> None:
    property_type = property_schema.get("type")

    if property_type not in {
        "string",
        "integer",
        "number",
        "boolean",
        "array",
        "object",
    }:
        raise ValueError(f"unsupported type for input property {path}")

    _validate_constraint_range(
        path,
        property_schema,
        minimum_key="minLength",
        maximum_key="maxLength",
    )
    _validate_constraint_range(
        path,
        property_schema,
        minimum_key="minimum",
        maximum_key="maximum",
    )
    _validate_constraint_range(
        path,
        property_schema,
        minimum_key="minItems",
        maximum_key="maxItems",
    )

    _validate_enum_values(path, property_schema)
    _validate_default_value(path, property_schema)

    items = property_schema.get("items")
    if isinstance(items, dict):
        _validate_property_schema(
            f"{path}.items",
            items,
        )

    nested_properties = property_schema.get("properties")
    if isinstance(nested_properties, dict):
        for name, nested_schema in nested_properties.items():
            if not isinstance(nested_schema, dict):
                raise ValueError("input_schema nested properties must contain schema objects")

            _validate_property_schema(
                f"{path}.{name}",
                nested_schema,
            )


def _validate_enum_values(
    path: str,
    property_schema: dict[str, Any],
) -> None:
    enum_values = property_schema.get("enum")

    if enum_values is None:
        return

    for index, value in enumerate(enum_values):
        errors = sorted(
            Draft202012Validator(
                property_schema,
                format_checker=_FORMAT_CHECKER,
            ).iter_errors(value),
            key=lambda error: tuple(str(segment) for segment in error.absolute_path),
        )

        if not errors:
            continue

        error = errors[0]

        raise ValueError(
            f"invalid enum value for input property {path} at index {index}: {error.message}"
        )


def _validate_default_value(
    name: str,
    property_schema: dict[str, Any],
) -> None:
    if "default" not in property_schema:
        return

    errors = sorted(
        Draft202012Validator(
            property_schema,
            format_checker=_FORMAT_CHECKER,
        ).iter_errors(property_schema["default"]),
        key=lambda error: tuple(str(segment) for segment in error.absolute_path),
    )
    if not errors:
        return

    error = errors[0]
    location = ".".join(str(segment) for segment in error.absolute_path)
    property_location = name if not location else f"{name}.{location}"
    raise ValueError(f"invalid default for input property {property_location}: {error.message}")


def _validate_capability_references(
    *,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    confirmation: dict[str, Any],
    execution: dict[str, Any],
    verification: dict[str, Any],
    response_handling: dict[str, Any],
) -> None:
    properties = set(input_schema["properties"])
    mappings = (
        execution["path_parameters"],
        execution["query_parameters"],
        execution["body_parameters"],
    )
    mapped_inputs = {
        rule["source"]
        for mapping in mappings
        for rule in mapping.values()
        if rule["source_type"] == "PARAMETER"
    }
    if mapped_inputs != properties:
        missing = sorted(properties - mapped_inputs)
        extra = sorted(mapped_inputs - properties)
        raise ValueError(
            "execution mappings must reference every input property exactly once; "
            f"missing={missing}, extra={extra}"
        )

    path_fields = set(re.findall(r"\{([a-z][a-z0-9_]*)\}", execution["path"]))
    mapped_path_fields = set(execution["path_parameters"])
    if path_fields != mapped_path_fields:
        raise ValueError(
            "execution path placeholders do not match path parameter mappings; "
            f"path={sorted(path_fields)}, mappings={sorted(mapped_path_fields)}"
        )

    response_targets = set(execution["response_mapping"])
    output_properties = set(output_schema["properties"])
    if response_targets != output_properties:
        missing = sorted(output_properties - response_targets)
        extra = sorted(response_targets - output_properties)
        raise ValueError(
            "response mappings must match output schema properties; "
            f"missing={missing}, extra={extra}"
        )
    verification_fields = set(verification["required_response_fields"])
    unknown_verification_fields = verification_fields - response_targets
    if unknown_verification_fields:
        raise ValueError(
            "verification fields must reference mapped response fields: "
            f"{sorted(unknown_verification_fields)}"
        )

    presentation_fields = {
        field
        for field in (
            response_handling["transaction_id_field"],
            response_handling["status_field"],
        )
        if field is not None
    }
    template_fields = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(
            response_handling["success_message_template"]
        )
        if field_name is not None
    }
    unknown_presentation_fields = (presentation_fields | template_fields) - response_targets
    if unknown_presentation_fields:
        raise ValueError(
            "response handling must reference mapped response fields: "
            f"{sorted(unknown_presentation_fields)}"
        )
    unverified_presentation_fields = (presentation_fields | template_fields) - verification_fields
    if unverified_presentation_fields:
        raise ValueError(
            "response handling fields must be verified before presentation: "
            f"{sorted(unverified_presentation_fields)}"
        )

    message_template = confirmation["message_template"]
    template_fields = (
        {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(message_template)
            if field_name is not None
        }
        if message_template is not None
        else set()
    )
    unknown_template_fields = template_fields - properties
    if unknown_template_fields:
        raise ValueError(
            f"confirmation template references unknown inputs: {sorted(unknown_template_fields)}"
        )
