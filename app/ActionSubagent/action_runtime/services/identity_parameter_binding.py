from __future__ import annotations

from typing import Any

from action_runtime.capability_registry import CapabilityRegistry, RegisteredCapability
from action_runtime.domain.delegation import DelegatedUserContext
from action_runtime.services.delegated_identity import IdentityParameterRule


class IdentityParameterBindingError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class IdentityParameterBinder:
    """Bind application identities before any parameter validation or confirmation mutation."""

    def __init__(self, *, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def validate_rules(self, rules: tuple[IdentityParameterRule, ...]) -> None:
        seen = set()
        for rule in rules:
            if not rule.capability_ids or len(set(rule.capability_ids)) != len(rule.capability_ids):
                raise IdentityParameterBindingError("identity_binding_config_invalid")
            for capability_id in rule.capability_ids:
                key = (rule.application_id, capability_id, rule.parameter)
                entries = [
                    entry
                    for entry in self.registry.list_active(rule.application_id)
                    if entry.capability.id == capability_id
                ]
                if key in seen or not entries:
                    raise IdentityParameterBindingError("identity_binding_config_invalid")
                seen.add(key)
                if any(
                    entry.capability.input_schema.get("properties", {})
                    .get(rule.parameter, {})
                    .get("type")
                    != "string"
                    for entry in entries
                ):
                    raise IdentityParameterBindingError("identity_binding_config_invalid")

    def bind(
        self,
        *,
        entry: RegisteredCapability,
        parameters: dict[str, Any],
        user_context: DelegatedUserContext,
        rules: tuple[IdentityParameterRule, ...],
        removed_parameters: list[str] | None = None,
    ) -> dict[str, Any]:
        self.validate_rules(rules)
        bound = dict(parameters)
        for rule in rules:
            if (
                rule.application_id != entry.capability.application_id
                or entry.capability.id not in rule.capability_ids
            ):
                continue
            value = user_context.application_user_ids.get(rule.application_id)
            if value is None:
                raise IdentityParameterBindingError("identity_mapping_required")
            if rule.parameter in (removed_parameters or []) or (
                rule.parameter in bound and bound[rule.parameter] != value
            ):
                raise IdentityParameterBindingError("identity_parameter_conflict")
            bound[rule.parameter] = value
        return bound
