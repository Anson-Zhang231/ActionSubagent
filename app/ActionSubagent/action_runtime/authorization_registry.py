from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from action_runtime.domain.authorization import (
    AuthorizationPolicy,
    DataScope,
    ResourceOwnershipPolicy,
)
from action_runtime.domain.identity import IdentityStatus, Permission, Role, User


class AuthorizationRegistryError(ValueError):
    pass


class IdentityNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class PermissionGrant:
    permission: Permission
    application_ids: tuple[str, ...]
    capability_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.application_ids) != len(set(self.application_ids)):
            raise ValueError("permission application_ids must not contain duplicates")
        if len(self.capability_ids) != len(set(self.capability_ids)):
            raise ValueError("permission capability_ids must not contain duplicates")
        if bool(self.application_ids) != bool(self.capability_ids):
            raise ValueError("permission application and capability scopes must both be set")


@dataclass(frozen=True, slots=True)
class RegisteredRole:
    role: Role
    data_scope: DataScope


class AuthorizationRegistry:
    def __init__(
        self,
        *,
        users: dict[str, User],
        roles: dict[str, RegisteredRole],
        permissions: dict[str, PermissionGrant],
        policies: dict[tuple[str, str], AuthorizationPolicy],
    ) -> None:
        self.users = users
        self.roles = roles
        self.permissions = permissions
        self.policies = policies
        self._validate_references()

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        additional_policy_directories: list[Path] | None = None,
    ) -> AuthorizationRegistry:
        try:
            identity = json.loads((directory / "identities.json").read_text(encoding="utf-8"))
            _validate_schema("identity-registry.schema.json", identity)
            policy_items: list[dict[str, Any]] = []
            for policy_directory in [directory, *(additional_policy_directories or [])]:
                policy_data = json.loads(
                    (policy_directory / "policies.json").read_text(encoding="utf-8")
                )
                _validate_schema("authorization-policy.schema.json", policy_data)
                policy_items.extend(policy_data["policies"])
            permission_items = identity["permissions"]
            role_items = identity["roles"]
            user_items = identity["users"]
            collections = (permission_items, role_items, user_items)
            if not all(isinstance(items, list) for items in collections):
                raise TypeError("identity registry collections must be arrays")
            _require_unique_ids(permission_items, "permission")
            _require_unique_ids(role_items, "role")
            _require_unique_ids(user_items, "user")
            permissions = {
                item["id"]: PermissionGrant(
                    permission=Permission(
                        id=item["id"],
                        name=item["name"],
                        description=item["description"],
                        status=IdentityStatus(item["status"]),
                        created_at=_datetime(item["created_at"]),
                        updated_at=_datetime(item["updated_at"]),
                    ),
                    application_ids=tuple(item["application_ids"]),
                    capability_ids=tuple(item["capability_ids"]),
                )
                for item in permission_items
            }
            roles = {
                item["id"]: RegisteredRole(
                    role=Role(
                        id=item["id"],
                        name=item["name"],
                        description=item["description"],
                        status=IdentityStatus(item["status"]),
                        permission_ids=tuple(item["permission_ids"]),
                        created_at=_datetime(item["created_at"]),
                        updated_at=_datetime(item["updated_at"]),
                    ),
                    data_scope=DataScope(item["data_scope"]),
                )
                for item in role_items
            }
            users = {
                item["id"]: User(
                    id=item["id"],
                    external_subject_id=item["external_subject_id"],
                    display_name=item["display_name"],
                    email=item.get("email"),
                    status=IdentityStatus(item["status"]),
                    role_ids=tuple(item["role_ids"]),
                    organization_id=item["organization_id"],
                    department_id=item["department_id"],
                    created_at=_datetime(item["created_at"]),
                    updated_at=_datetime(item["updated_at"]),
                )
                for item in user_items
            }
            policies = {}
            for item in policy_items:
                resource = item.get("resource_ownership")
                policy = AuthorizationPolicy(
                    application_id=item["application_id"],
                    capability_id=item["capability_id"],
                    allowed_organizations=tuple(item["allowed_organizations"]),
                    allowed_departments=tuple(item["allowed_departments"]),
                    resource_ownership=(
                        ResourceOwnershipPolicy(
                            parameter=resource["parameter"],
                            lookup_path=resource["lookup_path"],
                            owner_field=resource["owner_field"],
                        )
                        if resource is not None
                        else None
                    ),
                )
                key = (policy.application_id, policy.capability_id)
                if key in policies:
                    raise AuthorizationRegistryError(f"duplicate authorization policy: {key}")
                policies[key] = policy
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            if isinstance(error, AuthorizationRegistryError):
                raise
            raise AuthorizationRegistryError(f"invalid authorization registry: {error}") from error
        return cls(users=users, roles=roles, permissions=permissions, policies=policies)

    def resolve_user(self, user_id: str) -> User:
        try:
            return self.users[user_id]
        except KeyError as error:
            raise IdentityNotFoundError(user_id) from error

    def resolve_policy(self, application_id: str, capability_id: str) -> AuthorizationPolicy:
        try:
            return self.policies[(application_id, capability_id)]
        except KeyError as error:
            raise AuthorizationRegistryError(
                f"authorization policy is missing: {application_id}/{capability_id}"
            ) from error

    def validate_capability_catalog(self, capability_registry: Any) -> None:
        active = {
            (entry.capability.application_id, entry.capability.id): entry.capability
            for entry in capability_registry.list_active()
        }
        missing_policies = set(active) - set(self.policies)
        if missing_policies:
            raise AuthorizationRegistryError(
                f"active capabilities are missing authorization policies: {missing_policies}"
            )
        unknown_policies = set(self.policies) - set(active)
        if unknown_policies:
            raise AuthorizationRegistryError(
                f"authorization policies reference unknown capabilities: {unknown_policies}"
            )
        for key, policy in self.policies.items():
            capability = active[key]
            resource = policy.resource_ownership
            if resource is not None:
                properties = capability.input_schema.get("properties", {})
                if resource.parameter not in properties:
                    raise AuthorizationRegistryError(
                        f"resource policy references unknown parameter: {key}/{resource.parameter}"
                    )
                placeholder = "{" + resource.parameter + "}"
                if placeholder not in resource.lookup_path:
                    raise AuthorizationRegistryError(
                        f"resource lookup path is missing placeholder {placeholder}: {key}"
                    )
        for permission_id, grant in self.permissions.items():
            for application_id in grant.application_ids:
                for capability_id in grant.capability_ids:
                    if (application_id, capability_id) not in active:
                        raise AuthorizationRegistryError(
                            "permission scope references unknown capability: "
                            f"{permission_id}/{application_id}/{capability_id}"
                        )

    def _validate_references(self) -> None:
        for registered in self.roles.values():
            unknown = set(registered.role.permission_ids) - set(self.permissions)
            if unknown:
                raise AuthorizationRegistryError(f"role references unknown permissions: {unknown}")
        for user in self.users.values():
            unknown = set(user.role_ids) - set(self.roles)
            if unknown:
                raise AuthorizationRegistryError(f"user references unknown roles: {unknown}")


def _datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _require_unique_ids(items: list[Any], kind: str) -> None:
    identifiers = [item.get("id") for item in items if isinstance(item, dict)]
    if len(identifiers) != len(items) or any(not isinstance(value, str) for value in identifiers):
        raise TypeError(f"{kind} entries must be objects with string IDs")
    if len(identifiers) != len(set(identifiers)):
        raise AuthorizationRegistryError(f"duplicate {kind} identity")


def _validate_schema(filename: str, definition: Any) -> None:
    schema_path = Path(__file__).with_name("schemas") / filename
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(definition), key=lambda item: list(item.absolute_path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "root"
        raise AuthorizationRegistryError(
            f"{filename} validation failed at {location}: {first.message}"
        )


__all__ = [
    "AuthorizationRegistry",
    "AuthorizationRegistryError",
    "IdentityNotFoundError",
    "PermissionGrant",
    "RegisteredRole",
]
