from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from action_runtime.capability_registry import RegisteredCapability
from action_runtime.domain.capability import (
    FieldMappingDefinition,
    MappingSourceType,
    MappingTargetType,
)


class DataMappingError(ValueError):
    """Raised when a configured request or response mapping cannot be applied."""


@dataclass(frozen=True, slots=True)
class MappedHttpRequest:
    path: str
    query: dict[str, Any]
    body: dict[str, Any]


class DataMappingService:
    """Transforms standard Action data using only Capability configuration."""

    def map_request(
        self,
        entry: RegisteredCapability,
        parameters: Mapping[str, Any],
    ) -> MappedHttpRequest:
        definition = entry.capability.execution
        path = definition.path
        for placeholder, rule in definition.path_parameters.items():
            value = self._resolve(rule, parameters, target=placeholder)
            if isinstance(value, (dict, list)):
                raise DataMappingError(f"path mapping '{placeholder}' must resolve to a scalar")
            path = path.replace("{" + placeholder + "}", quote(str(value), safe=""))

        query: dict[str, Any] = {}
        for target, rule in definition.query_parameters.items():
            value = self._resolve(rule, parameters, target=target)
            if value is not _OMIT:
                query[target] = value

        body: dict[str, Any] = {}
        for target, rule in definition.body_parameters.items():
            value = self._resolve(rule, parameters, target=target)
            if value is not _OMIT:
                self._set_path(body, target, value)
        return MappedHttpRequest(path=path, query=query, body=body)

    def map_response(
        self,
        entry: RegisteredCapability,
        response: Mapping[str, Any],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for target, rule in entry.capability.execution.response_mapping.items():
            value = self._resolve(rule, response, target=target)
            if value is not _OMIT:
                self._set_path(result, target, value)
        return result

    def _resolve(
        self,
        rule: FieldMappingDefinition,
        values: Mapping[str, Any],
        *,
        target: str,
    ) -> Any:
        if rule.source_type is MappingSourceType.CONSTANT:
            value = rule.source
        else:
            found, value = self._get_path(values, str(rule.source))
            if not found:
                if rule.has_default:
                    value = rule.default_value
                elif rule.required:
                    raise DataMappingError(
                        f"required source '{rule.source}' is missing for mapping '{target}'"
                    )
                else:
                    return _OMIT
        return self._convert(value, rule.target_type, target=target)

    @staticmethod
    def _get_path(values: Mapping[str, Any], path: str) -> tuple[bool, Any]:
        current: Any = values
        for segment in path.split("."):
            if not isinstance(current, Mapping) or segment not in current:
                return False, None
            current = current[segment]
        return True, current

    @staticmethod
    def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
        segments = path.split(".")
        current = target
        for segment in segments[:-1]:
            existing = current.setdefault(segment, {})
            if not isinstance(existing, dict):
                raise DataMappingError(f"mapping target path '{path}' conflicts with another field")
            current = existing
        if segments[-1] in current:
            raise DataMappingError(f"mapping target path '{path}' is defined more than once")
        current[segments[-1]] = value

    @staticmethod
    def _convert(value: Any, target_type: MappingTargetType | None, *, target: str) -> Any:
        if target_type is None:
            return value
        try:
            if target_type is MappingTargetType.STRING:
                if isinstance(value, (dict, list)):
                    raise TypeError
                return str(value)
            if target_type is MappingTargetType.INTEGER:
                if isinstance(value, bool):
                    raise TypeError
                converted = int(value)
                if isinstance(value, float) and not value.is_integer():
                    raise ValueError
                return converted
            if target_type is MappingTargetType.NUMBER:
                if isinstance(value, bool):
                    raise TypeError
                return float(value)
            if target_type is MappingTargetType.BOOLEAN:
                if isinstance(value, bool):
                    return value
                if isinstance(value, str) and value.lower() in {"true", "1"}:
                    return True
                if isinstance(value, str) and value.lower() in {"false", "0"}:
                    return False
                raise TypeError
            if target_type is MappingTargetType.OBJECT:
                if not isinstance(value, Mapping):
                    raise TypeError
                return dict(value)
            if target_type is MappingTargetType.ARRAY:
                if not isinstance(value, list):
                    raise TypeError
                return value
        except (TypeError, ValueError, OverflowError) as error:
            raise DataMappingError(
                f"mapping '{target}' could not convert value to {target_type.value}"
            ) from error
        return value


_OMIT = object()
