from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from action_runtime.capability_registry import CapabilityRegistry
from action_runtime.domain.application import (
    Application,
    ApplicationConnection,
    ApplicationStatus,
    AuthenticationDefinition,
    AuthenticationMethod,
)


class ApplicationRegistryError(ValueError):
    """Raised when an Application directory contains an invalid definition."""


class ApplicationNotFoundError(LookupError):
    """Raised when an active Application version cannot be resolved."""


@dataclass(frozen=True, slots=True)
class RegisteredApplication:
    application: Application
    definition: dict[str, Any]
    definition_hash: str


class ApplicationRegistry:
    def __init__(
        self,
        entries: list[RegisteredApplication],
        *,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        if not entries:
            raise ApplicationRegistryError("at least one Application definition is required")

        indexed: dict[tuple[str, str], RegisteredApplication] = {}
        for entry in entries:
            key = (entry.application.id, entry.application.version)
            if key in indexed:
                raise ApplicationRegistryError("duplicate Application identity: " + "/".join(key))
            indexed[key] = entry

        self._entries = indexed
        _validate_version_compatibility(indexed)
        self.revision = _canonical_hash(
            [
                {"identity": list(key), "definition_hash": entry.definition_hash}
                for key, entry in sorted(indexed.items())
            ]
        )

        if capability_registry is not None:
            self._validate_capability_references(capability_registry)

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        capability_registry: CapabilityRegistry | None = None,
    ) -> ApplicationRegistry:
        return cls.from_directories([directory], capability_registry=capability_registry)

    @classmethod
    def from_directories(
        cls,
        directories: list[Path],
        *,
        capability_registry: CapabilityRegistry | None = None,
    ) -> ApplicationRegistry:
        entries: list[RegisteredApplication] = []
        for directory in directories:
            if not directory.is_dir():
                raise ApplicationRegistryError(f"Application directory does not exist: {directory}")

            for path in sorted(directory.rglob("*.json")):
                try:
                    definition = json.loads(path.read_text(encoding="utf-8"))
                    application = _parse_application(definition)
                except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                    raise ApplicationRegistryError(
                        f"invalid Application {path}: {error}"
                    ) from error

                entries.append(
                    RegisteredApplication(
                        application=application,
                        definition=definition,
                        definition_hash=_canonical_hash(definition),
                    )
                )

        return cls(entries, capability_registry=capability_registry)

    def list_active(self) -> list[RegisteredApplication]:
        return [entry for _, entry in sorted(self._entries.items()) if entry.application.is_active]

    def list_latest_active(self) -> list[RegisteredApplication]:
        latest: dict[str, RegisteredApplication] = {}
        for entry in self.list_active():
            current = latest.get(entry.application.id)
            if current is None or _version_key(entry.application.version) > _version_key(
                current.application.version
            ):
                latest[entry.application.id] = entry
        return [latest[key] for key in sorted(latest)]

    def resolve(
        self,
        *,
        application_id: str,
        version: str | None = None,
    ) -> RegisteredApplication:
        candidates = [
            entry
            for (entry_application_id, entry_version), entry in self._entries.items()
            if entry_application_id == application_id
            and (version is None or entry_version == version)
            and entry.application.is_active
        ]
        if not candidates:
            reference = application_id if version is None else f"{application_id}/{version}"
            raise ApplicationNotFoundError(reference)
        return max(candidates, key=lambda item: _version_key(item.application.version))

    def _validate_capability_references(self, capability_registry: CapabilityRegistry) -> None:
        active_capabilities_by_application: dict[str, set[str]] = {}
        for entry in capability_registry.list_active():
            active_capabilities_by_application.setdefault(
                entry.capability.application_id, set()
            ).add(entry.capability.id)

        active_applications = {entry.application.id: entry for entry in self.list_latest_active()}
        unknown_application_ids = sorted(
            set(active_capabilities_by_application) - set(active_applications)
        )
        if unknown_application_ids:
            raise ApplicationRegistryError(
                "active Capabilities reference unregistered Applications: "
                f"{unknown_application_ids}"
            )

        for application_id, entry in active_applications.items():
            declared_actions = set(entry.application.supported_actions)
            registered_actions = active_capabilities_by_application.get(application_id, set())
            if declared_actions != registered_actions:
                missing = sorted(registered_actions - declared_actions)
                unavailable = sorted(declared_actions - registered_actions)
                raise ApplicationRegistryError(
                    f"Application {application_id} supported_actions mismatch; "
                    f"missing={missing}, unavailable={unavailable}"
                )


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_version_compatibility(
    entries: dict[tuple[str, str], RegisteredApplication],
) -> None:
    families: dict[str, list[RegisteredApplication]] = {}
    for entry in entries.values():
        families.setdefault(entry.application.id, []).append(entry)
    for application_id, family in families.items():
        ordered = sorted(family, key=lambda item: _version_key(item.application.version))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            old = previous.application
            new = current.application
            if _version_key(old.version)[0] != _version_key(new.version)[0]:
                continue
            removed_actions = set(old.supported_actions) - set(new.supported_actions)
            if removed_actions:
                raise ApplicationRegistryError(
                    f"{application_id}/{new.version} removes supported actions without an "
                    f"Application major version bump: {sorted(removed_actions)}"
                )
            if old.connection.authentication.method != new.connection.authentication.method:
                raise ApplicationRegistryError(
                    f"{application_id}/{new.version} changes authentication method without an "
                    "Application major version bump"
                )


def _version_key(version: str) -> tuple[int, int, int, int, str]:
    core_and_suffix = version.split("+", 1)[0]
    core, separator, prerelease = core_and_suffix.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    return major, minor, patch, 1 if not separator else 0, prerelease


def _parse_application(definition: dict[str, Any]) -> Application:
    expected_fields = {
        "id",
        "name",
        "description",
        "owner",
        "environment",
        "status",
        "version",
        "connection",
        "supported_actions",
        "created_at",
        "updated_at",
    }
    actual_fields = set(definition)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        extra = sorted(actual_fields - expected_fields)
        raise ValueError(f"root fields mismatch; missing={missing}, extra={extra}")

    _version_key(definition["version"])
    connection = definition["connection"]
    required_connection_fields = {"base_url", "authentication"}
    allowed_connection_fields = required_connection_fields | {"user_id_map_reference"}
    if (
        not required_connection_fields <= set(connection)
        or set(connection) - allowed_connection_fields
    ):
        raise ValueError("connection fields are invalid")

    authentication = connection["authentication"]
    authentication_fields = set(authentication)
    required_authentication_fields = {"method", "scopes"}
    allowed_authentication_fields = required_authentication_fields | {"secret_reference"}
    if not required_authentication_fields <= authentication_fields:
        raise ValueError("authentication must contain method and scopes")
    if authentication_fields - allowed_authentication_fields:
        raise ValueError("authentication contains unsupported fields")

    return Application(
        id=definition["id"],
        name=definition["name"],
        description=definition["description"],
        owner=definition["owner"],
        environment=definition["environment"],
        status=ApplicationStatus(definition["status"]),
        version=definition["version"],
        connection=ApplicationConnection(
            base_url=connection["base_url"],
            user_id_map_reference=connection.get("user_id_map_reference"),
            authentication=AuthenticationDefinition(
                method=AuthenticationMethod(authentication["method"]),
                secret_reference=authentication.get("secret_reference"),
                scopes=tuple(authentication["scopes"]),
            ),
        ),
        supported_actions=tuple(definition["supported_actions"]),
        created_at=datetime.fromisoformat(definition["created_at"].replace("Z", "+00:00")),
        updated_at=datetime.fromisoformat(definition["updated_at"].replace("Z", "+00:00")),
    )


__all__ = [
    "ApplicationNotFoundError",
    "ApplicationRegistry",
    "ApplicationRegistryError",
    "RegisteredApplication",
]
