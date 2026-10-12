from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


class IdentityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


def validate_identifier(*, field_name: str, value: str) -> None:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must use the standard identifier format")


def validate_text(*, field_name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def validate_identifier_collection(
    *, field_name: str, values: tuple[str, ...], allow_empty: bool = False
) -> None:
    if not allow_empty and not values:
        raise ValueError(f"{field_name} must not be empty")

    for value in values:
        validate_identifier(field_name=f"{field_name} item", value=value)

    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")


def validate_timestamps(*, created_at: datetime, updated_at: datetime) -> None:
    for field_name, value in (("created_at", created_at), ("updated_at", updated_at)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")

    if updated_at < created_at:
        raise ValueError("updated_at must not be before created_at")


@dataclass(frozen=True, slots=True)
class Permission:
    """
    A permission that may be required by a Capability.

    The ID is referenced by Capability PermissionDefinition.
    """

    id: str
    name: str
    description: str
    status: IdentityStatus
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_identifier(field_name="permission_id", value=self.id)
        validate_text(field_name="permission name", value=self.name)
        validate_text(field_name="permission description", value=self.description)
        validate_timestamps(created_at=self.created_at, updated_at=self.updated_at)

    @property
    def is_active(self) -> bool:
        return self.status is IdentityStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class Role:
    """
    A named collection of Permission IDs.

    Permission records are resolved by the authorization layer later.
    """

    id: str
    name: str
    description: str
    status: IdentityStatus
    permission_ids: tuple[str, ...]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_identifier(field_name="role_id", value=self.id)
        validate_text(field_name="role name", value=self.name)
        validate_text(field_name="role description", value=self.description)
        validate_identifier_collection(field_name="permission_ids", values=self.permission_ids)
        validate_timestamps(created_at=self.created_at, updated_at=self.updated_at)

    @property
    def is_active(self) -> bool:
        return self.status is IdentityStatus.ACTIVE

    def contains_permission(self, permission_id: str) -> bool:
        return permission_id in self.permission_ids


@dataclass(frozen=True, slots=True)
class User:
    """
    A platform user with assigned Role IDs.

    Authentication credentials are not stored in this model.
    """

    id: str
    external_subject_id: str
    display_name: str
    email: str | None
    status: IdentityStatus
    role_ids: tuple[str, ...]
    organization_id: str
    department_id: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_identifier(field_name="user_id", value=self.id)
        validate_text(field_name="external_subject_id", value=self.external_subject_id)
        validate_text(field_name="display_name", value=self.display_name)

        if self.email is not None:
            normalized_email = self.email.lower().strip()
            if not normalized_email or "@" not in normalized_email:
                raise ValueError("email must be a valid non-empty address")

        validate_identifier_collection(field_name="role_ids", values=self.role_ids)
        validate_identifier(field_name="organization_id", value=self.organization_id)
        validate_identifier(field_name="department_id", value=self.department_id)
        validate_timestamps(created_at=self.created_at, updated_at=self.updated_at)

    @property
    def is_active(self) -> bool:
        return self.status is IdentityStatus.ACTIVE
