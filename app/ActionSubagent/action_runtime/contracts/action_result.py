from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class StandardActionResult(BaseModel):
    """Application-independent terminal Action response exposed to clients."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    transaction_id: str | None
    status: str
    message: str
    error_code: str | None
    retryable: bool
    data: dict[str, Any]
