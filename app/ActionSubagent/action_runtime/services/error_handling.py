from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any

from action_runtime.domain.action_session import ActionError, ActionStage


class ErrorHandlingService:
    """Normalizes target-application failures into the platform error contract."""

    _default_code = "application_error"
    _default_message = "The target application rejected the action."
    _http_error_defaults = {
        400: (
            "target_bad_request",
            "Target API reported a bad request.",
        ),
        401: (
            "target_authentication_failed",
            "Target API authentication failed: missing or invalid credentials.",
        ),
        500: ("target_internal_server_error", "Target API reported an internal server error."),
        503: ("target_service_unavailable", "Target API is unavailable."),
    }

    def from_application_response(
        self,
        *,
        status: int | None,
        body: Mapping[str, Any],
        retryable_http_statuses: Collection[int],
        fallback_message_fields: Collection[str] = (),
    ) -> ActionError:
        nested = body.get("detail")
        payload = nested if isinstance(nested, Mapping) else body
        message = nested if isinstance(nested, str) and nested.strip() else payload.get("message")
        if nested is None and message == "error":
            for field in fallback_message_fields:
                candidate = body.get(field)
                if isinstance(candidate, str) and candidate.strip() and candidate != "error":
                    message = candidate
                    break
        code = self._normalize_code(payload.get("code"))
        safe_message = self._safe_message(message)
        if status in self._http_error_defaults:
            fallback_code, fallback_message = self._http_error_defaults[status]
            if code == self._default_code:
                code = fallback_code
            if safe_message in {self._default_message, "error"}:
                safe_message = fallback_message
        return ActionError(
            code=code,
            message=safe_message,
            stage=ActionStage.EXECUTE,
            retryable=status is not None and status in retryable_http_statuses,
            details={"http_status": status},
        )

    def _normalize_code(self, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            return self._default_code
        normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
        if not normalized or not normalized[0].isalpha():
            return self._default_code
        return normalized[:100]

    def _safe_message(self, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            return self._default_message
        if "<" in value or ">" in value:
            return self._default_message
        return value.strip()[:500]


__all__ = ["ErrorHandlingService"]
