from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from action_runtime.contracts.capability_discovery import CapabilityDiscoveryResult
from action_runtime.domain.authorization import AuthenticatedPrincipal
from action_runtime.domain.conversation import Conversation
from action_runtime.services.action_sessions import (
    InMemoryActionSessionService,
    SessionRecord,
    SessionUpdateError,
    SessionVersionConflictError,
)
from action_runtime.services.capability_discovery import CapabilityDiscoveryService
from action_runtime.services.delegated_identity import DelegatedActionContext
from action_runtime.services.intent_interpretation import (
    IntentInterpretation,
    IntentInterpretationService,
    InvalidIntentOutputError,
)


class ConversationNotFoundError(LookupError):
    pass


@dataclass
class ConversationRecord:
    conversation: Conversation
    messages: list[dict[str, str]] = field(default_factory=list)
    candidates: list[dict[str, str]] = field(default_factory=list)
    session_ids: list[UUID] = field(default_factory=list)
    last_result: ConversationResult | None = None
    lock: Lock = field(default_factory=Lock)


@dataclass
class ConversationResult:
    conversation_id: UUID
    conversation_version: int
    interpretation: IntentInterpretation
    record: SessionRecord | None = None
    discovery: CapabilityDiscoveryResult | None = None


class ConversationService:
    """Process turns using bounded, user-owned in-memory conversation context."""

    def __init__(self) -> None:
        self._records: dict[UUID, ConversationRecord] = {}
        self._lock = Lock()

    def _resolve(
        self, principal: AuthenticatedPrincipal, conversation_id: UUID | None
    ) -> ConversationRecord:
        with self._lock:
            if conversation_id is None:
                conversation = Conversation.start(user_id=principal.user_id, channel="agent")
                record = ConversationRecord(conversation)
                self._records[conversation.id] = record
                return record
            record = self._records.get(conversation_id)
            if record is None or record.conversation.user_id != principal.user_id:
                raise ConversationNotFoundError("Conversation is unavailable")
            return record

    def handle(
        self,
        *,
        principal: AuthenticatedPrincipal,
        text: str,
        trace_id: str,
        sessions: InMemoryActionSessionService,
        intent_service: IntentInterpretationService,
        discovery_service: CapabilityDiscoveryService,
        conversation_id: UUID | None = None,
        expected_version: int | None = None,
        timezone: str = "UTC",
        allow_new_action: bool = True,
        delegation: DelegatedActionContext | None = None,
    ) -> ConversationResult:
        if not text.strip() or len(text) > 5000:
            raise SessionUpdateError("A message must contain between 1 and 5000 characters")
        try:
            zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise SessionUpdateError("Unknown timezone") from error
        record = self._resolve(principal, conversation_id)
        if not record.lock.acquire(blocking=False):
            raise SessionVersionConflictError("Another message is being processed")
        try:
            conversation = record.conversation
            if expected_version is not None and conversation.version != expected_version:
                raise SessionVersionConflictError("Conversation version has changed")
            active = (
                sessions.get(conversation.active_action_session_id)
                if conversation.active_action_session_id
                else None
            )
            active_version = active.session.version if active else None
            context: dict[str, Any] = {
                "current_time": datetime.now(zone).isoformat(),
                "timezone": timezone,
                "recent_messages": record.messages[-12:],
                "pending_candidates": record.candidates,
                "active_action": None,
            }
            if active:
                summary = {
                    "application_id": active.session.application_id,
                    "capability_id": active.session.capability_id,
                    "capability_version": active.session.capability_version,
                    "parameters": dict(active.session.collected_parameters),
                    "missing_parameters": list(active.missing_parameters),
                    "stage": active.session.stage.value,
                    "outcome": active.session.outcome.value,
                }
                context["last_action" if active.session.is_terminal else "active_action"] = summary
            choice = _choice_index(text, len(record.candidates))
            if choice is not None:
                interpretation = IntentInterpretation(
                    outcome="capability_selected",
                    confidence=1,
                    **record.candidates[choice],
                )
            elif (
                active
                and not active.session.is_terminal
                and text.strip().casefold()
                in {
                    "yes",
                    "ok",
                    "okay",
                    "confirm",
                    "confirmed",
                    "approve",
                    "好的",
                    "确认",
                    "是的",
                }
            ):
                interpretation = IntentInterpretation(
                    outcome="clarification_required",
                    confidence=1,
                    message="Use the confirmation controls to confirm the current parameters.",
                )
            else:
                interpretation = intent_service.interpret(user_text=text, context=context)
            result = ConversationResult(conversation.id, conversation.version, interpretation)
            if interpretation.outcome == "capability_selected":
                if interpretation.operation == "continue_action":
                    if active is None or active.session.is_terminal:
                        raise SessionUpdateError("No active action is available to continue")
                    if (
                        interpretation.application_id != active.session.application_id
                        or interpretation.capability_id != active.session.capability_id
                        or interpretation.capability_version != active.session.capability_version
                    ):
                        raise InvalidIntentOutputError("Continuation changed the pinned capability")
                    if not interpretation.parameters and not interpretation.removed_parameters:
                        interpretation = IntentInterpretation(
                            outcome="clarification_required",
                            confidence=1,
                            message="Provide parameter changes or use the confirmation controls.",
                        )
                        result.interpretation = interpretation
                        result.record = active
                    else:
                        result.record = sessions.add_parameters(
                            session_id=active.session.id,
                            expected_version=active_version,
                            parameters=interpretation.parameters,
                            removed_parameters=interpretation.removed_parameters,
                            principal=principal,
                            delegation=delegation,
                        )
                else:
                    if active and not allow_new_action:
                        raise SessionUpdateError("Start a new task for a different action")
                    result.record = sessions.create(
                        principal=principal,
                        trace_id=trace_id,
                        application_id=interpretation.application_id or "",
                        capability_id=interpretation.capability_id or "",
                        capability_version=interpretation.capability_version,
                        parameters=interpretation.parameters,
                        conversation_id=conversation.id,
                        user_request=text,
                        delegation=delegation,
                    )
                    record.session_ids.append(result.record.session.id)
                    conversation.active_action_session_id = result.record.session.id
            elif interpretation.outcome == "capability_discovery":
                result.discovery = discovery_service.discover(
                    application_id=interpretation.application_id
                )
            elif active and not active.session.is_terminal:
                result.record = active

            record.candidates = [item.model_dump() for item in interpretation.candidates]
            if record.candidates:
                choices = []
                for number, candidate in enumerate(interpretation.candidates, 1):
                    entry = sessions.registry.resolve(
                        application_id=candidate.application_id,
                        capability_id=candidate.capability_id,
                        version=candidate.capability_version,
                    )
                    choices.append(f"{number}. {entry.capability.name}")
                interpretation = interpretation.model_copy(
                    update={"message": "Please select an action:\n" + "\n".join(choices)}
                )
                result.interpretation = interpretation
            reply = interpretation.message or interpretation.outcome
            if result.discovery is not None:
                reply = result.discovery.message
            elif result.record and interpretation.outcome == "capability_selected":
                reply = result.record.confirmation_summary or (
                    "Required parameters: " + ", ".join(result.record.missing_parameters)
                )
            record.messages = (
                record.messages
                + [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": reply},
                ]
            )[-12:]
            conversation.version += 1
            conversation.updated_at = conversation.last_activity_at = datetime.now(UTC)
            result.conversation_version = conversation.version
            record.last_result = result
            return result
        finally:
            record.lock.release()

    def get(
        self, *, principal: AuthenticatedPrincipal, conversation_id: UUID
    ) -> ConversationResult:
        record = self._resolve(principal, conversation_id)
        if not record.lock.acquire(blocking=False):
            raise SessionVersionConflictError("Another message is being processed")
        try:
            if record.last_result is None:
                raise ConversationNotFoundError("Conversation has no completed turn")
            return record.last_result
        finally:
            record.lock.release()


def _choice_index(text: str, count: int) -> int | None:
    """Resolve exact ordinal replies against the server-rendered choice order."""
    normalized = text.strip().casefold().rstrip("。.!！")
    chinese = "一二三四五六七八"
    for index in range(count):
        if normalized in {
            str(index + 1),
            f"option {index + 1}",
            f"第{index + 1}个",
            f"第{chinese[index]}个",
            f"选第{chinese[index]}个",
        }:
            return index
    return None
