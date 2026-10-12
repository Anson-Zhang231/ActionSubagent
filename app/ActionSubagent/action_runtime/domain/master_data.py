from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
SEMANTIC_VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)$"
)
PATH_PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]+)\}")


class MasterDataStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class MasterDataHttpMethod(StrEnum):
    GET = "GET"


@dataclass(frozen=True, slots=True)
class MasterDataSearchDefinition:
    operation_id: str
    method: MasterDataHttpMethod
    path: str
    query_parameter: str
    items_field: str

    def __post_init__(self) -> None:
        if not IDENTIFIER_PATTERN.fullmatch(self.operation_id):
            raise ValueError("search operation_id must use the standard identifier format")

        if not self.operation_id.startswith("search_"):
            raise ValueError("search operation_id must start with 'search_'")

        for field_name, value in (
            ("query_parameter", self.query_parameter),
            ("items_field", self.items_field),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"search {field_name} must use the standard identifier format")

        _validate_relative_path(self.path, operation="search")

        if PATH_PLACEHOLDER_PATTERN.findall(self.path):
            raise ValueError("search path must not contain path placeholders")


@dataclass(frozen=True, slots=True)
class MasterDataGetDefinition:
    operation_id: str
    method: MasterDataHttpMethod
    path: str
    path_parameter: str

    def __post_init__(self) -> None:
        if not IDENTIFIER_PATTERN.fullmatch(self.operation_id):
            raise ValueError("get operation_id must use the standard identifier format")

        if not self.operation_id.startswith("get_"):
            raise ValueError("get operation_id must start with 'get_'")

        if not IDENTIFIER_PATTERN.fullmatch(self.path_parameter):
            raise ValueError("get path_parameter must use the standard identifier format")

        _validate_relative_path(self.path, operation="get")

        placeholders = PATH_PLACEHOLDER_PATTERN.findall(self.path)

        if placeholders != [self.path_parameter]:
            raise ValueError(
                "get path must contain exactly one placeholder matching path_parameter"
            )


@dataclass(frozen=True, slots=True)
class MasterDataValidationRule:
    field: str
    source_id: str
    require_active: bool
    message: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("field", self.field),
            ("source_id", self.source_id),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} must use the standard identifier format")
        if not isinstance(self.require_active, bool):
            raise ValueError("require_active must be a boolean")
        if not self.message.strip():
            raise ValueError("master data validation message must not be empty")


@dataclass(frozen=True, slots=True)
class MasterDataDefinition:
    id: str
    application_id: str
    version: str

    name: str
    description: str
    status: MasterDataStatus

    value_field: str
    label_field: str
    active_field: str | None

    search: MasterDataSearchDefinition
    get: MasterDataGetDefinition

    timeout_seconds: float

    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for field_name, value in (
            ("id", self.id),
            ("application_id", self.application_id),
            ("value_field", self.value_field),
            ("label_field", self.label_field),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} must use the standard identifier format")

        if self.active_field is not None:
            if not IDENTIFIER_PATTERN.fullmatch(self.active_field):
                raise ValueError("active_field must use the standard identifier format")

        if not SEMANTIC_VERSION_PATTERN.fullmatch(self.version):
            raise ValueError("version must use semantic versioning")

        for field_name, value in (
            ("name", self.name),
            ("description", self.description),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty")

        if self.search.operation_id == self.get.operation_id:
            raise ValueError("search and get operation ids must be different")

        if self.timeout_seconds <= 0 or self.timeout_seconds > 30:
            raise ValueError("timeout_seconds must be greater than zero and at most 30")

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
        return self.status is MasterDataStatus.ACTIVE


def _validate_relative_path(
    path: str,
    *,
    operation: str,
) -> None:
    if not path.startswith("/"):
        raise ValueError(f"{operation} path must start with '/'")

    if "://" in path:
        raise ValueError(f"{operation} path must not contain a base URL")

    if "?" in path or "#" in path:
        raise ValueError(f"{operation} path must not contain query or fragment")


__all__ = [
    "MasterDataDefinition",
    "MasterDataGetDefinition",
    "MasterDataHttpMethod",
    "MasterDataSearchDefinition",
    "MasterDataStatus",
    "MasterDataValidationRule",
]
