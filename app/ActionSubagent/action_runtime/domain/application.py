from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlparse

IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ApplicationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class AuthenticationMethod(StrEnum):
    NONE = "NONE"
    API_KEY = "API_KEY"
    BASIC = "BASIC"
    BEARER_TOKEN = "BEARER_TOKEN"
    OAUTH2_CLIENT_CREDENTIALS = "OAUTH2_CLIENT_CREDENTIALS"


@dataclass(frozen=True, slots=True)
class AuthenticationDefinition:
    """
    Describes how a Connector obtains authentication credentials.

    Actual secrets must be stored outside the Application definition.
    """

    method: AuthenticationMethod
    secret_reference: str | None = None
    scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.method is AuthenticationMethod.NONE:
            if self.secret_reference is not None:
                raise ValueError(
                    "secret_reference must be empty when authentication method is NONE"
                )

            if self.scopes:
                raise ValueError("scopes must be empty when authentication method is NONE")

        elif not self.secret_reference:
            raise ValueError("secret_reference is required for authenticated applications")

        if any(not scope.strip() for scope in self.scopes):
            raise ValueError("authentication scopes must not contain empty values")

        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("authentication scopes must not contain duplicates")


@dataclass(frozen=True, slots=True)
class ApplicationConnection:
    """
    Defines the Application-level connection information.

    Capability definitions provide relative execution paths.
    """

    base_url: str
    authentication: AuthenticationDefinition
    user_id_map_reference: str | None = None

    def __post_init__(self) -> None:
        parsed_url = urlparse(self.base_url)

        if parsed_url.scheme not in {"http", "https"}:
            raise ValueError("base_url must use http or https")

        if not parsed_url.netloc:
            raise ValueError("base_url must contain a host")

        if parsed_url.query or parsed_url.fragment:
            raise ValueError("base_url must not contain a query or fragment")

        if self.base_url.endswith("/"):
            raise ValueError("base_url must not end with '/'")

        if self.user_id_map_reference is not None and not self.user_id_map_reference.startswith(
            "env:"
        ):
            raise ValueError("user_id_map_reference must be an env: reference")


@dataclass(frozen=True, slots=True)
class Application:
    """
    Domain definition of a business application available to Agents.

    Application-specific actions are represented by Capability
    definitions and must not be implemented in the Orchestrator.
    """

    id: str
    name: str
    description: str
    owner: str

    environment: str
    status: ApplicationStatus
    version: str

    connection: ApplicationConnection
    supported_actions: tuple[str, ...]

    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not IDENTIFIER_PATTERN.fullmatch(self.id):
            raise ValueError(
                "application id must start with a lowercase letter "
                "and contain only lowercase letters, numbers, "
                "and underscores"
            )

        for field_name, value in (
            ("name", self.name),
            ("description", self.description),
            ("owner", self.owner),
            ("environment", self.environment),
            ("version", self.version),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty")

        if not self.supported_actions:
            raise ValueError("supported_actions must not be empty")

        if any(not IDENTIFIER_PATTERN.fullmatch(action_id) for action_id in self.supported_actions):
            raise ValueError("supported action ids must use the standard identifier format")

        if len(set(self.supported_actions)) != len(self.supported_actions):
            raise ValueError("supported_actions must not contain duplicates")

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
        return self.status is ApplicationStatus.ACTIVE
