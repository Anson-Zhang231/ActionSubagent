from __future__ import annotations

import base64
import json
import os
from typing import Any

from action_runtime.domain.application import AuthenticationDefinition, AuthenticationMethod


class ApplicationCredentialError(RuntimeError):
    """Raised when an Application credential definition cannot be resolved."""


class ApplicationCredentialAccessDeniedError(PermissionError):
    """Raised when no delegated credential exists for the current user."""


class EnvironmentApplicationUserIdResolver:
    """Map a trusted platform user ID to an external application user ID."""

    def resolve(self, reference: str | None, *, user_id: str) -> str:
        if not reference:
            raise ApplicationCredentialError("Application user ID map is not configured")

        scheme, separator, variable_name = reference.partition(":")
        if scheme != "env" or not separator or not variable_name:
            raise ApplicationCredentialError("User ID map must use an env: reference")

        configured = os.getenv(variable_name)
        if configured is None or not configured.strip():
            raise ApplicationCredentialError("Application user ID map is unavailable")

        try:
            mapping = json.loads(configured)
        except json.JSONDecodeError as error:
            raise ApplicationCredentialError("Application user ID map is invalid JSON") from error

        if not isinstance(mapping, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) or not value.strip()
            for key, value in mapping.items()
        ):
            raise ApplicationCredentialError(
                "Application user ID map must contain nonempty string IDs"
            )

        external_user_id = mapping.get(user_id)
        if external_user_id is None:
            raise ApplicationCredentialAccessDeniedError(
                "No external user ID is configured for this user"
            )

        return external_user_id.strip()


class EnvironmentApplicationCredentialResolver:
    """Resolve Application authentication headers from an external environment secret.

    Application definitions contain only an ``env:VARIABLE`` reference. The referenced value may
    be a shared credential or a JSON object keyed by platform user ID for delegated credentials.
    No Application ID, endpoint, or credential value is embedded in the Agent runtime.
    """

    def headers_for(
        self,
        authentication: AuthenticationDefinition,
        *,
        user_id: str,
    ) -> dict[str, str]:
        if authentication.method is AuthenticationMethod.NONE:
            return {}

        credential = self._credential(authentication.secret_reference, user_id=user_id)
        method = authentication.method
        if method is AuthenticationMethod.BEARER_TOKEN:
            token = self._string_value(credential, field="token")
            return {"Authorization": f"Bearer {token}"}
        if method is AuthenticationMethod.API_KEY:
            if isinstance(credential, str):
                return {"X-API-Key": credential}
            header_name = self._required_string(credential, "header_name")
            value = self._required_string(credential, "value")
            return {header_name: value}
        if method is AuthenticationMethod.BASIC:
            username = self._required_string(credential, "username")
            password = self._required_string(credential, "password")
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
            return {"Authorization": f"Basic {encoded}"}
        if method is AuthenticationMethod.OAUTH2_CLIENT_CREDENTIALS:
            token = self._string_value(credential, field="access_token")
            return {"Authorization": f"Bearer {token}"}
        raise ApplicationCredentialError(f"Unsupported authentication method: {method}")

    @staticmethod
    def _credential(secret_reference: str | None, *, user_id: str) -> Any:
        if not secret_reference:
            raise ApplicationCredentialError("Authenticated Application has no secret reference")
        scheme, separator, reference = secret_reference.partition(":")
        if not separator or scheme != "env" or not reference:
            raise ApplicationCredentialError(
                "Only env: secret references are supported by this runtime"
            )
        configured = os.getenv(reference)
        if configured is None or not configured.strip():
            raise ApplicationCredentialError(
                f"Application credential environment variable is unavailable: {reference}"
            )
        try:
            parsed = json.loads(configured)
        except json.JSONDecodeError:
            parsed = configured
        if isinstance(parsed, dict) and user_id in parsed:
            parsed = parsed[user_id]
        elif isinstance(parsed, dict) and EnvironmentApplicationCredentialResolver._looks_like_map(
            parsed
        ):
            raise ApplicationCredentialAccessDeniedError(
                "No Application credential is configured for this user"
            )
        if not isinstance(parsed, (str, dict)):
            raise ApplicationCredentialError(
                "Application credential configuration must be a string or object"
            )
        return parsed

    @staticmethod
    def _looks_like_map(value: dict[str, Any]) -> bool:
        credential_fields = {
            "token",
            "access_token",
            "header_name",
            "value",
            "username",
            "password",
        }
        return not bool(set(value) & credential_fields)

    @staticmethod
    def _string_value(value: Any, *, field: str) -> str:
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            return EnvironmentApplicationCredentialResolver._required_string(value, field)
        raise ApplicationCredentialError("Application credential has an invalid value")

    @staticmethod
    def _required_string(value: Any, field: str) -> str:
        if not isinstance(value, dict):
            raise ApplicationCredentialError("Application credential must be an object")
        result = value.get(field)
        if not isinstance(result, str) or not result:
            raise ApplicationCredentialError(
                f"Application credential field must be a nonempty string: {field}"
            )
        return result


__all__ = [
    "ApplicationCredentialAccessDeniedError",
    "ApplicationCredentialError",
    "EnvironmentApplicationCredentialResolver",
    "EnvironmentApplicationUserIdResolver",
]
