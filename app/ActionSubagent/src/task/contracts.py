from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from action_runtime.domain.delegation import DelegatedUserContext
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    TypeAdapter,
)


def _protobuf_integer(value: Any) -> Any:
    # Protobuf Struct represents JSON numbers as floats. The wire schema still
    # uses integer: JSON Schema accepts 3.0 and rejects strings and booleans.
    if type(value) is float and value.is_integer():
        return int(value)
    return value


SessionVersion = Annotated[int, Field(strict=True, ge=0), BeforeValidator(_protobuf_integer)]

_HYPHENATED_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UuidInput = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=re.compile(
            rf"^(?:[0-9a-fA-F]{{32}}|{_HYPHENATED_UUID}|"
            rf"urn:uuid:{_HYPHENATED_UUID}|\{{{_HYPHENATED_UUID}\}})(?![\s\S])"
        ),
    ),
]
_UUID_INPUT_ADAPTER = TypeAdapter(UuidInput)


def _uuid_wire_input(value: Any) -> Any:
    if isinstance(value, UUID):
        return value
    return _UUID_INPUT_ADAPTER.validate_python(value)


ConfirmationId = Annotated[
    UUID, BeforeValidator(_uuid_wire_input, json_schema_input_type=UuidInput)
]


class TextTaskInput(BaseModel):
    """Initial request or text continuation; no structured operation fields."""

    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["1.0"]
    user_context: DelegatedUserContext
    operation: None = None
    parameters: None = None
    expected_version: None = None
    confirmation_id: None = None
    decision: None = None


class ParameterTaskInput(TextTaskInput):
    """Provide nonempty parameters for a specific existing session version."""

    operation: Literal["provide_parameters"]
    parameters: dict[str, Any] = Field(min_length=1)
    expected_version: SessionVersion


class ConfirmationTaskInput(TextTaskInput):
    """Explicit decision bound to an existing confirmation and session version."""

    operation: Literal["confirmation"]
    expected_version: SessionVersion
    confirmation_id: ConfirmationId
    decision: Literal["confirm", "cancel"]


class DelegatedTaskInput(RootModel[TextTaskInput | ParameterTaskInput | ConfirmationTaskInput]):
    """Single public wire contract; model_dump emits the original flat JSON shape."""

    @property
    def user_context(self) -> DelegatedUserContext:
        return self.root.user_context

    @property
    def operation(self) -> str | None:
        return self.root.operation

    @property
    def expected_version(self) -> int | None:
        return self.root.expected_version

    @property
    def contract_version(self) -> str:
        return self.root.contract_version

    @property
    def parameters(self) -> dict[str, Any] | None:
        return self.root.parameters

    @property
    def confirmation_id(self) -> UUID | None:
        return self.root.confirmation_id

    @property
    def decision(self) -> str | None:
        return self.root.decision


class SubAgentTaskState(StrEnum):
    """Protocol-neutral states exposed by the subagent boundary."""

    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SubAgentTaskResult:
    """One deterministic result returned to any external agent adapter."""

    state: SubAgentTaskState
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    artifact_name: str = "action-session-state"
