from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from action_runtime.domain.action_session import ActionError
from action_runtime.domain.capability import ConnectorType, HttpMethod
from action_runtime.domain.identity import validate_identifier, validate_text


class ExecutionStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    RETURNED = "RETURNED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class Execution:
    """
    Records one attempt to execute an authorized Capability.

    RETURNED means that the target system returned a response. It does
    not mean that the business Action succeeded; Verify decides that.
    """

    id: UUID
    session_id: UUID
    confirmation_id: UUID | None

    application_id: str
    capability_id: str
    capability_version: str

    connector: ConnectorType
    http_method: HttpMethod
    request_path: str
    request_hash: str
    idempotency_key: str

    attempt: int
    timeout_seconds: float
    status: ExecutionStatus
    request_sent: bool

    external_request_id: str | None
    http_status: int | None
    response_data: Mapping[str, Any]
    error: ActionError | None

    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    version: int

    @classmethod
    def create(
        cls,
        *,
        session_id: UUID,
        confirmation_id: UUID | None,
        application_id: str,
        capability_id: str,
        capability_version: str,
        connector: ConnectorType,
        http_method: HttpMethod,
        request_path: str,
        request_hash: str,
        idempotency_key: str,
        attempt: int,
        timeout_seconds: float,
    ) -> Execution:
        execution = cls(
            id=uuid4(),
            session_id=session_id,
            confirmation_id=confirmation_id,
            application_id=application_id,
            capability_id=capability_id,
            capability_version=capability_version,
            connector=connector,
            http_method=http_method,
            request_path=request_path,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            attempt=attempt,
            timeout_seconds=timeout_seconds,
            status=ExecutionStatus.CREATED,
            request_sent=False,
            external_request_id=None,
            http_status=None,
            response_data={},
            error=None,
            created_at=datetime.now(UTC),
            started_at=None,
            finished_at=None,
            version=1,
        )

        execution.validate_invariants()
        return execution

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ExecutionStatus.RETURNED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
            ExecutionStatus.UNKNOWN,
        }

    @property
    def requires_verification(self) -> bool:
        return self.status in {
            ExecutionStatus.RETURNED,
            ExecutionStatus.TIMED_OUT,
            ExecutionStatus.UNKNOWN,
        }

    def validate_invariants(self) -> None:
        for field_name, value in (
            ("application_id", self.application_id),
            ("capability_id", self.capability_id),
        ):
            validate_identifier(field_name=field_name, value=value)

        for field_name, value in (
            ("capability_version", self.capability_version),
            ("request_path", self.request_path),
            ("request_hash", self.request_hash),
            ("idempotency_key", self.idempotency_key),
        ):
            validate_text(field_name=field_name, value=value)

        if not self.request_path.startswith("/"):
            raise ValueError("request_path must start with '/'")

        if "://" in self.request_path:
            raise ValueError("request_path must not contain a base URL")

        if "?" in self.request_path or "#" in self.request_path:
            raise ValueError("request_path must not contain query or fragment")

        if self.attempt < 1:
            raise ValueError("attempt must be greater than or equal to 1")

        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        if self.version < 1:
            raise ValueError("version must be greater than or equal to 1")

        for field_name, value in (
            ("created_at", self.created_at),
            ("started_at", self.started_at),
            ("finished_at", self.finished_at),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} must be timezone-aware")

        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at must not be before created_at")

        if self.finished_at is not None:
            if self.started_at is None:
                raise ValueError("finished_at requires started_at")

            if self.finished_at < self.started_at:
                raise ValueError("finished_at must not be before started_at")

        if self.external_request_id is not None:
            validate_text(field_name="external_request_id", value=self.external_request_id)

        if self.http_status is not None and not 100 <= self.http_status <= 599:
            raise ValueError("http_status must be between 100 and 599")

        if self.status is ExecutionStatus.CREATED:
            if self.request_sent:
                raise ValueError("a CREATED execution cannot have request_sent=True")

            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("a CREATED execution cannot contain execution timestamps")

            if self.http_status is not None or self.response_data or self.error is not None:
                raise ValueError("a CREATED execution cannot contain a result")

        if self.status is ExecutionStatus.RUNNING:
            if self.started_at is None:
                raise ValueError("a RUNNING execution requires started_at")

            if self.finished_at is not None:
                raise ValueError("a RUNNING execution cannot contain finished_at")

            if self.http_status is not None or self.response_data or self.error is not None:
                raise ValueError("a RUNNING execution cannot contain a result")

        if self.status is ExecutionStatus.RETURNED:
            if not self.request_sent:
                raise ValueError("a RETURNED execution requires request_sent=True")

            if self.finished_at is None:
                raise ValueError("a RETURNED execution requires finished_at")

            if self.http_status is None:
                raise ValueError("a RETURNED execution requires http_status")

            if self.error is not None:
                raise ValueError("a RETURNED execution cannot contain an execution error")

        if self.status is ExecutionStatus.FAILED:
            if self.finished_at is None:
                raise ValueError("a FAILED execution requires finished_at")

            if self.error is None:
                raise ValueError("a FAILED execution requires an error")

            if self.http_status is not None or self.response_data:
                raise ValueError("a FAILED execution cannot contain a returned response")

        if self.status is ExecutionStatus.TIMED_OUT:
            if self.finished_at is None:
                raise ValueError("a TIMED_OUT execution requires finished_at")

            if self.http_status is not None or self.response_data:
                raise ValueError("a TIMED_OUT execution cannot contain a returned response")

        if self.status is ExecutionStatus.UNKNOWN:
            if not self.request_sent:
                raise ValueError("an UNKNOWN execution requires request_sent=True")

            if self.finished_at is None:
                raise ValueError("an UNKNOWN execution requires finished_at")

            if self.http_status is not None or self.response_data:
                raise ValueError("an UNKNOWN execution cannot contain a returned response")
