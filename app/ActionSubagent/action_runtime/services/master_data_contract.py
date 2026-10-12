"""Optional Master Data validation boundary shared by action orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from action_runtime.domain.master_data import MasterDataValidationRule


class MasterDataLookupUnavailableError(RuntimeError):
    pass


class MasterDataValidator(Protocol):
    def validate(
        self,
        *,
        application_id: str,
        rules: tuple[MasterDataValidationRule, ...],
        parameters: Mapping[str, Any],
        user_id: str,
        trace_id: str,
    ) -> list[dict[str, str]]: ...
