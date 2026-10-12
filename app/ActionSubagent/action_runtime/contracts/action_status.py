from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ActionTrackingStatus(StrEnum):
    """Stable client-facing states for polling an Action."""

    SUBMITTED = "SUBMITTED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ActionStatusResponse(BaseModel):
    """Application-independent status returned for an Action or transaction ID."""

    model_config = ConfigDict(extra="forbid")

    action_id: UUID
    transaction_id: str | None
    status: ActionTrackingStatus
    outcome: str
    stage: str
    stage_status: str
    waiting_reason: str | None
    application_id: str
    capability_id: str
    capability_version: str
    api_version: str
    input_schema_version: str
    input_schema_hash: str
    output_schema_version: str
    output_schema_hash: str
    submitted_at: datetime
    processing_started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
    poll_after_seconds: int | None
    error: dict[str, Any] | None
