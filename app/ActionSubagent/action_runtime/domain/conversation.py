from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from action_runtime.domain.identity import validate_identifier, validate_text, validate_timestamps


class ConversationStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


@dataclass(slots=True)
class Conversation:
    """
    Represents one chat conversation between a user and the platform.

    Messages and ActionSessions are stored separately and reference
    the Conversation by its ID.
    """

    id: UUID
    user_id: str
    channel: str
    external_conversation_id: str | None
    title: str | None
    status: ConversationStatus
    active_action_session_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    expires_at: datetime | None

    @classmethod
    def start(
        cls,
        *,
        user_id: str,
        channel: str,
        external_conversation_id: str | None = None,
        title: str | None = None,
        expires_at: datetime | None = None,
    ) -> Conversation:
        now = datetime.now(UTC)

        conversation = cls(
            id=uuid4(),
            user_id=user_id,
            channel=channel,
            external_conversation_id=external_conversation_id,
            title=title,
            status=ConversationStatus.OPEN,
            active_action_session_id=None,
            version=1,
            created_at=now,
            updated_at=now,
            last_activity_at=now,
            expires_at=expires_at,
        )

        conversation.validate_invariants()
        return conversation

    @property
    def is_open(self) -> bool:
        return self.status is ConversationStatus.OPEN

    @property
    def can_accept_messages(self) -> bool:
        return self.is_open

    def validate_invariants(self) -> None:
        validate_identifier(field_name="user_id", value=self.user_id)
        validate_identifier(field_name="channel", value=self.channel)
        validate_timestamps(created_at=self.created_at, updated_at=self.updated_at)

        if self.external_conversation_id is not None:
            validate_text(
                field_name="external_conversation_id", value=self.external_conversation_id
            )

        if self.title is not None:
            validate_text(field_name="title", value=self.title)

        if self.version < 1:
            raise ValueError("version must be greater than or equal to 1")

        if self.last_activity_at.tzinfo is None or self.last_activity_at.utcoffset() is None:
            raise ValueError("last_activity_at must be timezone-aware")

        if self.last_activity_at < self.created_at:
            raise ValueError("last_activity_at must not be before created_at")

        if self.last_activity_at > self.updated_at:
            raise ValueError("last_activity_at must not be after updated_at")

        if self.expires_at is not None:
            if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
                raise ValueError("expires_at must be timezone-aware")

            if self.expires_at <= self.created_at:
                raise ValueError("expires_at must be after created_at")

        if not self.is_open and self.active_action_session_id is not None:
            raise ValueError("a closed or expired conversation cannot have an active ActionSession")
