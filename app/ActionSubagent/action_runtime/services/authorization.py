from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request
from uuid import uuid4

from action_runtime.application_registry import ApplicationRegistry
from action_runtime.authorization_registry import AuthorizationRegistry, IdentityNotFoundError
from action_runtime.capability_registry import RegisteredCapability, canonical_hash
from action_runtime.domain.authorization import (
    AuthenticatedPrincipal,
    AuthorizationDecision,
    DataScope,
    ResourceOwnershipPolicy,
)
from action_runtime.domain.capability import PermissionMatchMode
from action_runtime.services.application_credentials import (
    ApplicationCredentialAccessDeniedError,
    ApplicationCredentialError,
    EnvironmentApplicationCredentialResolver,
)
from action_runtime.services.http_transport import open_no_redirect


class AuthenticationRequiredError(PermissionError):
    pass


class AuthenticationForbiddenError(PermissionError):
    pass


class AuthorizationServiceUnavailableError(RuntimeError):
    pass


class ResourceAccessVerifier(Protocol):
    def get_resource(
        self,
        *,
        application_id: str,
        policy: ResourceOwnershipPolicy,
        parameters: dict[str, Any],
        user_id: str,
        trace_id: str,
    ) -> dict[str, Any] | None: ...


class AuthenticationService:
    def __init__(self, registry: AuthorizationRegistry) -> None:
        self.registry = registry
        configured = os.getenv("AGENT_IDENTITY_TOKEN_MAP")
        try:
            if configured:
                token_map = json.loads(configured)
            elif os.getenv("APP_ENV", "development") == "development":
                token_map = {
                    "agent-user-001-token": "user-001",
                    "agent-user-002-token": "user-002",
                    "agent-admin-token": "admin-001",
                    "agent-viewer-token": "viewer-001",
                    "agent-inactive-token": "inactive-001",
                    "agent-partner-token": "partner-001",
                }
            else:
                raise ValueError("AGENT_IDENTITY_TOKEN_MAP is required outside development")
        except json.JSONDecodeError as error:
            raise ValueError("AGENT_IDENTITY_TOKEN_MAP must be valid JSON") from error
        if not isinstance(token_map, dict) or not token_map:
            raise ValueError("AGENT_IDENTITY_TOKEN_MAP must be a nonempty object")
        self._token_hashes = {
            hashlib.sha256(token.encode()).hexdigest(): user_id
            for token, user_id in token_map.items()
            if isinstance(token, str) and token and isinstance(user_id, str) and user_id
        }
        if len(self._token_hashes) != len(token_map):
            raise ValueError("AGENT_IDENTITY_TOKEN_MAP contains invalid entries")

    def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        if not authorization_header:
            raise AuthenticationRequiredError("A bearer token is required")
        scheme, separator, token = authorization_header.partition(" ")
        if not separator or scheme.casefold() != "bearer" or not token.strip():
            raise AuthenticationRequiredError("A valid bearer token is required")
        user_id = self._token_hashes.get(hashlib.sha256(token.strip().encode()).hexdigest())
        if user_id is None:
            raise AuthenticationRequiredError("The bearer token is invalid")
        try:
            user = self.registry.resolve_user(user_id)
        except IdentityNotFoundError as error:
            raise AuthenticationRequiredError("The bearer token identity is unavailable") from error
        if not user.is_active:
            raise AuthenticationForbiddenError("The user account is inactive")

        active_roles = []
        for role_id in user.role_ids:
            registered = self.registry.roles[role_id]
            if registered.role.is_active:
                active_roles.append(registered)
        if not active_roles:
            raise AuthenticationForbiddenError("The user has no active roles")

        permission_ids = frozenset(
            permission_id
            for role in active_roles
            for permission_id in role.role.permission_ids
            if self.registry.permissions[permission_id].permission.is_active
        )
        scope_rank = {
            DataScope.SELF: 0,
            DataScope.DEPARTMENT: 1,
            DataScope.ORGANIZATION: 2,
            DataScope.ALL: 3,
        }
        data_scope = max((role.data_scope for role in active_roles), key=scope_rank.__getitem__)
        return AuthenticatedPrincipal(
            user_id=user.id,
            external_subject_id=user.external_subject_id,
            organization_id=user.organization_id,
            department_id=user.department_id,
            role_ids=tuple(role.role.id for role in active_roles),
            permission_ids=permission_ids,
            data_scope=data_scope,
        )


class LazyAuthenticationService:
    """Load the optional HTTP adapter's token configuration only when it authenticates."""

    def __init__(self, registry: AuthorizationRegistry) -> None:
        self.registry = registry
        self._service: AuthenticationService | None = None

    def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        if self._service is None:
            self._service = AuthenticationService(self.registry)
        return self._service.authenticate(authorization_header)


class HttpResourceAccessVerifier:
    def __init__(
        self,
        *,
        application_registry: ApplicationRegistry,
        credential_resolver: EnvironmentApplicationCredentialResolver | None = None,
    ) -> None:
        self.application_registry = application_registry
        self.credential_resolver = credential_resolver or EnvironmentApplicationCredentialResolver()

    def get_resource(
        self,
        *,
        application_id: str,
        policy: ResourceOwnershipPolicy,
        parameters: dict[str, Any],
        user_id: str,
        trace_id: str,
    ) -> dict[str, Any] | None:
        raw_value = parameters.get(policy.parameter)
        if raw_value is None:
            return None
        path = policy.lookup_path.replace(
            "{" + policy.parameter + "}", quote(str(raw_value), safe="")
        )
        application = self.application_registry.resolve(application_id=application_id).application
        try:
            authentication_headers = self.credential_resolver.headers_for(
                application.connection.authentication,
                user_id=user_id,
            )
        except ApplicationCredentialAccessDeniedError:
            return None
        except ApplicationCredentialError as error:
            raise AuthorizationServiceUnavailableError(
                "resource authorization credential is unavailable"
            ) from error
        request = Request(
            application.connection.base_url.rstrip("/") + path,
            headers={**authentication_headers, "X-Trace-ID": trace_id},
            method="GET",
        )
        try:
            with open_no_redirect(request, timeout=5) as response:
                data = json.loads(response.read())
        except HTTPError as error:
            if error.code in {401, 403, 404}:
                return None
            raise AuthorizationServiceUnavailableError(
                f"resource authorization lookup returned HTTP {error.code}"
            ) from error
        except (TimeoutError, URLError, json.JSONDecodeError) as error:
            raise AuthorizationServiceUnavailableError(
                "resource authorization lookup is unavailable"
            ) from error
        if not isinstance(data, dict):
            raise AuthorizationServiceUnavailableError(
                "resource authorization lookup returned an invalid response"
            )
        return data


class AuthorizationService:
    def __init__(
        self,
        *,
        registry: AuthorizationRegistry,
        resource_verifier: ResourceAccessVerifier,
        decision_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        self.registry = registry
        self.resource_verifier = resource_verifier
        self.decision_ttl = decision_ttl
        self._decisions: dict[str, AuthorizationDecision] = {}

    def can_access_application(
        self,
        *,
        principal: AuthenticatedPrincipal,
        application_id: str,
    ) -> bool:
        if principal.data_scope is DataScope.ALL:
            return True
        return any(
            permission_id in self.registry.permissions
            and application_id in self.registry.permissions[permission_id].application_ids
            for permission_id in principal.permission_ids
        )

    def authorize(
        self,
        *,
        principal: AuthenticatedPrincipal,
        entry: RegisteredCapability,
        parameters: dict[str, Any],
        trace_id: str,
    ) -> AuthorizationDecision:
        reason = self._evaluate(
            principal=principal,
            entry=entry,
            parameters=parameters,
            trace_id=trace_id,
        )
        now = datetime.now(UTC)
        decision = AuthorizationDecision(
            id=f"decision_{uuid4().hex}",
            allowed=reason == "authorized",
            reason_code=reason,
            user_id=principal.user_id,
            application_id=entry.capability.application_id,
            capability_id=entry.capability.id,
            capability_version=entry.capability.version,
            parameter_hash=canonical_hash(parameters),
            issued_at=now,
            expires_at=now + self.decision_ttl,
        )
        self._decisions[decision.id] = decision
        return decision

    def reauthorize(
        self,
        *,
        decision_id: str,
        principal: AuthenticatedPrincipal,
        entry: RegisteredCapability,
        parameters: dict[str, Any],
        trace_id: str,
    ) -> AuthorizationDecision:
        previous = self._decisions.get(decision_id)
        now = datetime.now(UTC)
        if (
            previous is None
            or not previous.allowed
            or now >= previous.expires_at
            or previous.user_id != principal.user_id
            or previous.application_id != entry.capability.application_id
            or previous.capability_id != entry.capability.id
            or previous.capability_version != entry.capability.version
            or previous.parameter_hash != canonical_hash(parameters)
        ):
            return self._denied_binding_decision(principal, entry, parameters, now)
        return self.authorize(
            principal=principal,
            entry=entry,
            parameters=parameters,
            trace_id=trace_id,
        )

    def _evaluate(
        self,
        *,
        principal: AuthenticatedPrincipal,
        entry: RegisteredCapability,
        parameters: dict[str, Any],
        trace_id: str,
    ) -> str:
        capability = entry.capability
        required = set(capability.permissions.required_permissions)
        scoped_permissions = {
            permission_id
            for permission_id in principal.permission_ids
            if permission_id in self.registry.permissions
            and capability.application_id
            in self.registry.permissions[permission_id].application_ids
            and capability.id in self.registry.permissions[permission_id].capability_ids
        }
        if capability.permissions.match_mode is PermissionMatchMode.ALL:
            permission_allowed = required <= scoped_permissions
        else:
            permission_allowed = bool(required & scoped_permissions)
        if not permission_allowed:
            return "missing_action_permission"

        policy = self.registry.resolve_policy(capability.application_id, capability.id)
        if (
            policy.allowed_organizations
            and principal.organization_id not in policy.allowed_organizations
        ):
            return "organization_not_allowed"
        if policy.allowed_departments and principal.department_id not in policy.allowed_departments:
            return "department_not_allowed"

        ownership = policy.resource_ownership
        if ownership is not None and principal.data_scope is not DataScope.ALL:
            resource = self.resource_verifier.get_resource(
                application_id=capability.application_id,
                policy=ownership,
                parameters=parameters,
                user_id=principal.user_id,
                trace_id=trace_id,
            )
            if resource is None:
                return "resource_access_denied"
            if principal.data_scope is DataScope.SELF:
                if resource.get(ownership.owner_field) != principal.user_id:
                    return "resource_owner_mismatch"
            elif principal.data_scope is DataScope.DEPARTMENT:
                if resource.get("department_id") != principal.department_id:
                    return "resource_department_mismatch"
            elif principal.data_scope is DataScope.ORGANIZATION:
                if resource.get("organization_id") != principal.organization_id:
                    return "resource_organization_mismatch"
        return "authorized"

    def _denied_binding_decision(
        self,
        principal: AuthenticatedPrincipal,
        entry: RegisteredCapability,
        parameters: dict[str, Any],
        now: datetime,
    ) -> AuthorizationDecision:
        decision = AuthorizationDecision(
            id=f"decision_{uuid4().hex}",
            allowed=False,
            reason_code="authorization_decision_invalid",
            user_id=principal.user_id,
            application_id=entry.capability.application_id,
            capability_id=entry.capability.id,
            capability_version=entry.capability.version,
            parameter_hash=canonical_hash(parameters),
            issued_at=now,
            expires_at=now,
        )
        self._decisions[decision.id] = decision
        return decision


__all__ = [
    "AuthenticationForbiddenError",
    "AuthenticationRequiredError",
    "AuthenticationService",
    "AuthorizationService",
    "AuthorizationServiceUnavailableError",
    "HttpResourceAccessVerifier",
    "ResourceAccessVerifier",
]
