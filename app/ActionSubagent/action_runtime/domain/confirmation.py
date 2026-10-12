from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from action_runtime.domain.capability import RiskLevel
from action_runtime.domain.identity import validate_identifier, validate_text


class ConfirmationStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


@dataclass(slots=True)
class Confirmation:
    """
    Records the exact Action summary presented to a user.

    Only a hash of the one-time confirmation token is stored.
    """

    id: UUID
    session_id: UUID
    user_id: str

    application_id: str
    capability_id: str
    capability_version: str
    authorization_decision_id: str

    parameter_hash: str
    summary_hash: str
    token_hash: str

    risk_level: RiskLevel
    status: ConfirmationStatus

    requested_at: datetime
    expires_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None

    version: int

    @classmethod
    def request(
        cls,
        *,
        session_id: UUID,
        user_id: str,
        application_id: str,
        capability_id: str,
        capability_version: str,
        authorization_decision_id: str,
        parameter_hash: str,
        summary_hash: str,
        token_hash: str,
        risk_level: RiskLevel,
        expires_at: datetime,
    ) -> Confirmation:
        confirmation = cls(
            id=uuid4(),
            session_id=session_id,
            user_id=user_id,
            application_id=application_id,
            capability_id=capability_id,
            capability_version=capability_version,
            authorization_decision_id=authorization_decision_id,
            parameter_hash=parameter_hash,
            summary_hash=summary_hash,
            token_hash=token_hash,
            risk_level=risk_level,
            status=ConfirmationStatus.PENDING,
            requested_at=datetime.now(UTC),
            expires_at=expires_at,
            resolved_at=None,
            resolved_by=None,
            version=1,
        )

        confirmation.validate_invariants()
        return confirmation

    @property
    def is_pending(self) -> bool:
        return self.status is ConfirmationStatus.PENDING

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ConfirmationStatus.CONFIRMED,
            ConfirmationStatus.CANCELLED,
            ConfirmationStatus.EXPIRED,
            ConfirmationStatus.INVALIDATED,
        }

    @property
    def can_be_used(self) -> bool:
        return self.is_pending

    def validate_invariants(self) -> None:
        validate_text(field_name="user_id", value=self.user_id)

        for field_name, value in (
            ("application_id", self.application_id),
            ("capability_id", self.capability_id),
        ):
            validate_identifier(field_name=field_name, value=value)

        for field_name, value in (
            ("capability_version", self.capability_version),
            ("authorization_decision_id", self.authorization_decision_id),
            ("parameter_hash", self.parameter_hash),
            ("summary_hash", self.summary_hash),
            ("token_hash", self.token_hash),
        ):
            validate_text(field_name=field_name, value=value)

        if self.version < 1:
            raise ValueError("version must be greater than or equal to 1")

        for field_name, value in (
            ("requested_at", self.requested_at),
            ("expires_at", self.expires_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")

        if self.expires_at <= self.requested_at:
            raise ValueError("expires_at must be after requested_at")

        if self.resolved_at is not None:
            if self.resolved_at.tzinfo is None or self.resolved_at.utcoffset() is None:
                raise ValueError("resolved_at must be timezone-aware")

            if self.resolved_at < self.requested_at:
                raise ValueError("resolved_at must not be before requested_at")

        if self.resolved_by is not None:
            validate_text(field_name="resolved_by", value=self.resolved_by)

        if self.is_pending:
            if self.resolved_at is not None or self.resolved_by is not None:
                raise ValueError("a pending confirmation cannot contain resolution data")
        else:
            if self.resolved_at is None:
                raise ValueError("a resolved confirmation requires resolved_at")

        if self.status is ConfirmationStatus.EXPIRED and self.resolved_at is not None:
            if self.resolved_at < self.expires_at:
                raise ValueError("an expired confirmation cannot resolve before expires_at")

        if (
            self.status
            in {
                ConfirmationStatus.CONFIRMED,
                ConfirmationStatus.CANCELLED,
                ConfirmationStatus.INVALIDATED,
            }
            and self.resolved_by is None
        ):
            raise ValueError(f"{self.status.value} confirmation requires resolved_by")
