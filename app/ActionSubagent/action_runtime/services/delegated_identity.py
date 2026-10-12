from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from action_runtime.authorization_registry import AuthorizationRegistry, AuthorizationRegistryError
from action_runtime.domain.authorization import AuthenticatedPrincipal, DataScope
from action_runtime.domain.delegation import DelegatedUserContext, Identifier


class DelegatedIdentityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IdentityParameterRule:
    application_id: str
    capability_ids: tuple[str, ...]
    parameter: str


class _BindingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: Identifier
    capability_ids: list[Identifier] = Field(min_length=1)
    parameter: Identifier


class _DelegationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_mode: Literal["trusted_supervisor"]
    role_ids: list[Identifier] = Field(min_length=1)
    parameter_bindings: list[_BindingConfig]

    @model_validator(mode="after")
    def unique_roles(self) -> _DelegationConfig:
        if len(self.role_ids) != len(set(self.role_ids)):
            raise ValueError("Duplicate delegated roles")
        return self


class DelegatedIdentityResolver:
    """Project supervisor identity, optionally applying legacy local role grants."""

    def __init__(
        self,
        *,
        config_path: Path,
        identity_directory: Path,
        authorization_registry: AuthorizationRegistry | None,
        enforce_authorization: bool = True,
    ) -> None:
        self.config_path = config_path
        self.identity_directory = identity_directory
        self.authorization_registry = authorization_registry
        self.enforce_authorization = enforce_authorization
        if enforce_authorization:
            self._grants()
        else:
            self._config()

    def _config(self) -> _DelegationConfig:
        try:
            return _DelegationConfig.model_validate_json(
                self.config_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as error:
            raise DelegatedIdentityError(
                "Delegated identity configuration is unavailable"
            ) from error

    def _grants(self) -> tuple[tuple[str, ...], frozenset[str], DataScope]:
        if self.authorization_registry is None:
            raise DelegatedIdentityError("Delegated permission registry is unavailable")
        config = self._config()
        try:
            registry = AuthorizationRegistry.from_directory(self.identity_directory)
            roles = [registry.roles[role_id] for role_id in config.role_ids]
            if any(not role.role.is_active for role in roles):
                raise DelegatedIdentityError("Delegated role is unavailable")
            permissions = set()
            for role in roles:
                for permission_id in role.role.permission_ids:
                    grant = registry.permissions[permission_id]
                    active_grant = self.authorization_registry.permissions[permission_id]
                    if (
                        grant.application_ids != active_grant.application_ids
                        or grant.capability_ids != active_grant.capability_ids
                    ):
                        raise DelegatedIdentityError("Delegated permission scope is inconsistent")
                    if grant.permission.is_active and active_grant.permission.is_active:
                        permissions.add(permission_id)
            rank = {
                DataScope.SELF: 0,
                DataScope.DEPARTMENT: 1,
                DataScope.ORGANIZATION: 2,
                DataScope.ALL: 3,
            }
            scope = max((role.data_scope for role in roles), key=rank.__getitem__)
            return tuple(config.role_ids), frozenset(permissions), scope
        except (OSError, KeyError, AuthorizationRegistryError) as error:
            raise DelegatedIdentityError(
                "Delegated permission configuration is unavailable"
            ) from error

    def resolve(self, user_context: DelegatedUserContext) -> AuthenticatedPrincipal:
        if self.enforce_authorization:
            role_ids, permission_ids, scope = self._grants()
        else:
            # Retain correlation identity and binding; no local business grants.
            role_ids, permission_ids, scope = (), frozenset(), DataScope.SELF
        return AuthenticatedPrincipal(
            user_id=user_context.user_id,
            external_subject_id=f"delegated:{user_context.user_id}",
            organization_id=user_context.organization_id,
            department_id=user_context.department_id,
            role_ids=role_ids,
            permission_ids=permission_ids,
            data_scope=scope,
        )

    def binding_rules(self) -> tuple[IdentityParameterRule, ...]:
        return tuple(
            IdentityParameterRule(item.application_id, tuple(item.capability_ids), item.parameter)
            for item in self._config().parameter_bindings
        )


@dataclass(frozen=True, slots=True)
class DelegatedActionContext:
    user_context: DelegatedUserContext
    resolver: DelegatedIdentityResolver

    def current_principal(self) -> AuthenticatedPrincipal:
        return self.resolver.resolve(self.user_context)
