"""Shared registry and action-service composition for the independent API adapters."""

from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path
from threading import RLock
from typing import Any

from action_runtime.application_registry import ApplicationRegistry
from action_runtime.authorization_registry import AuthorizationRegistry
from action_runtime.capability_registry import CapabilityRegistry
from action_runtime.services.action_execution import ActionExecutionService
from action_runtime.services.application_credentials import (
    EnvironmentApplicationCredentialResolver,
)
from action_runtime.services.authorization import (
    AuthorizationService,
    HttpResourceAccessVerifier,
    LazyAuthenticationService,
)
from action_runtime.services.capability_discovery import CapabilityDiscoveryService
from action_runtime.services.conversations import ConversationService
from action_runtime.services.delegated_identity import DelegatedIdentityResolver
from action_runtime.services.identity_parameter_binding import IdentityParameterBinder
from action_runtime.services.intent_interpretation import (
    IntentInterpretationService,
    create_intent_interpreter,
)
from action_runtime.services.response_handling import ResponseHandlingService


def _default_config_directory() -> Path:
    configured = os.getenv("ACTION_CONFIG_DIRECTORY")
    return (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[1] / "tools/ai-service/config"
    )


def _catalog_directories(name: str, override: str) -> list[Path]:
    configured = os.getenv(override)
    if configured:
        return [Path(configured)]
    root = _default_config_directory()
    directories = [root / name]
    additional = os.getenv("ACTION_EXTRA_CONFIG_DIRECTORY")
    if additional:
        directories.append(Path(additional) / name)
    return directories


def _default_capability_directory() -> Path:
    return _catalog_directories("capabilities", "CAPABILITY_DIRECTORY")[0]


def _default_application_directory() -> Path:
    return _catalog_directories("applications", "APPLICATION_DIRECTORY")[0]


def _default_master_data_directory() -> Path | None:
    configured = os.getenv("MASTER_DATA_DIRECTORY")
    return Path(configured) if configured else None


def _default_authorization_directory() -> Path:
    configured = os.getenv("AUTHORIZATION_DIRECTORY")
    return Path(configured) if configured else _default_config_directory() / "authorization"


registry = CapabilityRegistry.from_directories(
    _catalog_directories("capabilities", "CAPABILITY_DIRECTORY")
)
application_registry = ApplicationRegistry.from_directories(
    _catalog_directories("applications", "APPLICATION_DIRECTORY"),
    capability_registry=registry,
)
application_credential_resolver = EnvironmentApplicationCredentialResolver()
_master_data_directory = _default_master_data_directory()
if _master_data_directory is None:
    if any(entry.capability.master_data_rules for entry in registry.list_active()):
        raise RuntimeError("Master Data rules require MASTER_DATA_DIRECTORY")
    master_data_registry = None
    master_data_lookup_service = None
    master_data_validation_service = None
else:
    factory_reference = os.getenv("MASTER_DATA_FACTORY")
    if not factory_reference or ":" not in factory_reference:
        raise RuntimeError("MASTER_DATA_FACTORY is required for Master Data")
    factory_module, factory_name = factory_reference.split(":", maxsplit=1)
    factory = getattr(import_module(factory_module), factory_name)
    (
        master_data_registry,
        master_data_lookup_service,
        master_data_validation_service,
    ) = factory(
        _master_data_directory,
        application_registry,
        registry,
        application_credential_resolver,
    )
_local_authorization_lock = RLock()


def _ensure_local_authorization() -> None:
    """Load the optional local HTTP adapter's existing business authorization lazily."""
    with _local_authorization_lock:
        if "authorization_registry" in globals():
            return
        local_registry = AuthorizationRegistry.from_directory(
            _default_authorization_directory(),
            additional_policy_directories=(
                [Path(extra_policy)]
                if (extra_policy := os.getenv("ACTION_EXTRA_POLICY_DIRECTORY"))
                else []
            ),
        )
        local_registry.validate_capability_catalog(registry)
        local_authorization = AuthorizationService(
            registry=local_registry,
            resource_verifier=HttpResourceAccessVerifier(
                application_registry=application_registry,
                credential_resolver=application_credential_resolver,
            ),
        )
        local_sessions = ActionExecutionService(
            registry,
            application_registry=application_registry,
            connector_simulation_header=os.getenv("CONNECTOR_SIMULATION_HEADER"),
            master_data_validator=master_data_validation_service,
            authorization_service=local_authorization,
            identity_parameter_binder=identity_parameter_binder,
        )
        globals().update(
            authorization_registry=local_registry,
            authentication_service=LazyAuthenticationService(local_registry),
            authorization_service=local_authorization,
            sessions=local_sessions,
        )


def __getattr__(name: str) -> Any:
    if name in {
        "authorization_registry",
        "authentication_service",
        "authorization_service",
        "sessions",
    }:
        _ensure_local_authorization()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_delegated_identity_resolver(
    *, enforce_authorization: bool = True
) -> DelegatedIdentityResolver:
    if os.getenv("A2A_IDENTITY_MODE", "trusted_supervisor") != "trusted_supervisor":
        raise ValueError("A2A_IDENTITY_MODE must be trusted_supervisor")
    if enforce_authorization:
        _ensure_local_authorization()
    return DelegatedIdentityResolver(
        config_path=Path(
            os.getenv(
                "DELEGATED_IDENTITY_CONFIG",
                str(_default_config_directory() / "authorization/delegated-identity.json"),
            )
        ),
        identity_directory=Path(
            os.getenv(
                "DELEGATED_AUTHORIZATION_DIRECTORY",
                str(_default_config_directory() / "authorization"),
            )
        ),
        authorization_registry=globals().get("authorization_registry")
        if enforce_authorization
        else None,
        enforce_authorization=enforce_authorization,
    )


identity_parameter_binder = IdentityParameterBinder(registry=registry)

discovery_service = CapabilityDiscoveryService(
    application_registry=application_registry,
    capability_registry=registry,
)
intent_service = IntentInterpretationService(
    registry=registry, interpreter=create_intent_interpreter()
)
response_handling_service = ResponseHandlingService()
conversation_service = ConversationService()
