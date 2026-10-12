from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

_ERROR_DETAILS: dict[str, tuple[str, str]] = {
    "enum": ("invalid_enum", "Value is not allowed"),
    "const": ("invalid_value", "Value is not allowed"),
    "minLength": ("too_short", "Value is too short"),
    "maxLength": ("too_long", "Value is too long"),
    "pattern": ("invalid_format", "Value format is invalid"),
    "format": ("invalid_format", "Value format is invalid"),
    "minimum": ("too_small", "Value is too small"),
    "exclusiveMinimum": ("too_small", "Value is too small"),
    "maximum": ("too_large", "Value is too large"),
    "exclusiveMaximum": ("too_large", "Value is too large"),
    "minItems": ("too_few_items", "Not enough items were provided"),
    "maxItems": ("too_many_items", "Too many items were provided"),
    "uniqueItems": ("duplicate_items", "Items must be unique"),
}


def validate_parameters(
    schema: dict[str, Any],
    parameters: dict[str, Any],
) -> list[dict[str, str]]:
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )

    errors = sorted(
        validator.iter_errors(parameters),
        key=lambda error: (
            ".".join(str(part) for part in error.absolute_path),
            str(error.validator),
        ),
    )

    issues: list[dict[str, str]] = []

    for error in errors:
        issues.extend(_issues_for_error(error))

    return _remove_duplicate_issues(issues)


def _issues_for_error(error: ValidationError) -> list[dict[str, str]]:
    if error.validator == "required":
        return _required_field_issues(error)

    if error.validator == "additionalProperties":
        return _additional_property_issues(error)

    if error.validator == "type":
        expected_type = error.validator_value

        if isinstance(expected_type, list):
            expected = " or ".join(str(value) for value in expected_type)
        else:
            expected = str(expected_type)

        return [
            {
                "field": _format_field_path(error.absolute_path),
                "code": "invalid_type",
                "message": f"Expected {expected}",
            }
        ]

    code, message = _ERROR_DETAILS.get(
        str(error.validator),
        ("invalid_value", "Value is invalid"),
    )

    return [
        {
            "field": _format_field_path(error.absolute_path),
            "code": code,
            "message": message,
        }
    ]


def _required_field_issues(error: ValidationError) -> list[dict[str, str]]:
    if not isinstance(error.instance, dict):
        return []

    required_fields = error.validator_value
    if not isinstance(required_fields, list):
        return []

    return [
        {
            "field": _format_field_path(
                [*error.absolute_path, field_name],
            ),
            "code": "required",
            "message": "Field is required",
        }
        for field_name in required_fields
        if field_name not in error.instance
    ]


def _additional_property_issues(
    error: ValidationError,
) -> list[dict[str, str]]:
    if not isinstance(error.instance, dict):
        return []

    properties = error.schema.get("properties", {})
    allowed_fields = set(properties)

    return [
        {
            "field": _format_field_path(
                [*error.absolute_path, field_name],
            ),
            "code": "unknown_field",
            "message": "Field is not allowed",
        }
        for field_name in sorted(set(error.instance) - allowed_fields)
    ]


def _format_field_path(path: Iterable[str | int]) -> str:
    result = ""

    for part in path:
        if isinstance(part, int):
            result = f"{result}[{part}]"
        elif result:
            result = f"{result}.{part}"
        else:
            result = part

    return result or "$"


def _remove_duplicate_issues(
    issues: list[dict[str, str]],
) -> list[dict[str, str]]:
    unique_issues: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for issue in issues:
        key = (
            issue["field"],
            issue["code"],
            issue["message"],
        )

        if key in seen:
            continue

        seen.add(key)
        unique_issues.append(issue)

    return unique_issues
