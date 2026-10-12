from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


class DataScope(StrEnum):
    SELF = "SELF"
    DEPARTMENT = "DEPARTMENT"
    ORGANIZATION = "ORGANIZATION"
    ALL = "ALL"


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    user_id: str
    external_subject_id: str
    organization_id: str
    department_id: str
    role_ids: tuple[str, ...]
    permission_ids: frozenset[str]
    data_scope: DataScope

    def __post_init__(self) -> None:
        for field_name, value in (
            ("user_id", self.user_id),
            ("organization_id", self.organization_id),
            ("department_id", self.department_id),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} must use the standard identifier format")


@dataclass(frozen=True, slots=True)
class ResourceOwnershipPolicy:
    parameter: str
    lookup_path: str
    owner_field: str

    def __post_init__(self) -> None:
        if not IDENTIFIER_PATTERN.fullmatch(self.parameter):
            raise ValueError("resource parameter must use the standard identifier format")
        if not self.lookup_path.startswith("/") or "://" in self.lookup_path:
            raise ValueError("resource lookup_path must be a relative HTTP path")
        if not IDENTIFIER_PATTERN.fullmatch(self.owner_field):
            raise ValueError("resource owner_field must use the standard identifier format")


@dataclass(frozen=True, slots=True)
class AuthorizationPolicy:
    application_id: str
    capability_id: str
    allowed_organizations: tuple[str, ...]
    allowed_departments: tuple[str, ...]
    resource_ownership: ResourceOwnershipPolicy | None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("application_id", self.application_id),
            ("capability_id", self.capability_id),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} must use the standard identifier format")
        for values in (self.allowed_organizations, self.allowed_departments):
            if len(values) != len(set(values)):
                raise ValueError("authorization policy lists must not contain duplicates")
            if any(not IDENTIFIER_PATTERN.fullmatch(value) for value in values):
                raise ValueError("authorization policy IDs use an invalid format")


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    id: str
    allowed: bool
    reason_code: str
    user_id: str
    application_id: str
    capability_id: str
    capability_version: str
    parameter_hash: str
    issued_at: datetime
    expires_at: datetime


__all__ = [
    "AuthenticatedPrincipal",
    "AuthorizationDecision",
    "AuthorizationPolicy",
    "DataScope",
    "ResourceOwnershipPolicy",
]
