from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from a2a.helpers.proto_helpers import (
    new_data_part,
    new_task_from_user_message,
    new_text_message,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    InvalidParamsError,
)
from action_runtime.capability_registry import CapabilityRegistry
from fastapi import FastAPI

from .. import __version__
from ..task.contracts import SubAgentTaskResult, SubAgentTaskState
from ..task.service import SubAgentTaskService
from .request_handler import DelegatedRequestHandler, resolve_task_owner


@dataclass(frozen=True, slots=True)
class A2AAdapter:
    """Objects retained for the lifetime of the ASGI application."""

    card: AgentCard
    handler: DefaultRequestHandler
    executor: ActionAgentExecutor


class ActionAgentExecutor(AgentExecutor):
    """Translate A2A messages and task events to the protocol-neutral core."""

    def __init__(self, task_service: SubAgentTaskService) -> None:
        self.task_service = task_service

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None or context.task_id is None or context.context_id is None:
            raise InvalidParamsError(message="A message, task ID, and context ID are required")

        task_input = context.call_context.state.get("action_task_input")
        if task_input is None:
            raise InvalidParamsError(message="A validated delegated identity is required")
        self.task_service.assert_task_access(
            task_id=context.task_id,
            context_id=context.context_id,
            user_id=task_input.user_context.user_id,
            user_context=task_input.user_context,
        )

        if context.current_task is None:
            await event_queue.enqueue_event(new_task_from_user_message(context.message))

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=context.task_id,
            context_id=context.context_id,
        )
        await updater.start_work(
            new_text_message(
                "The Action Agent is processing the delegated task.",
                context_id=context.context_id,
                task_id=context.task_id,
            )
        )

        command = task_input.model_dump(
            mode="json", exclude_none=True, exclude={"user_context", "contract_version"}
        )
        trace_id = context.metadata.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id.strip():
            trace_id = f"a2a-{uuid4().hex}"

        result = await asyncio.to_thread(
            self.task_service.handle,
            task_id=context.task_id,
            context_id=context.context_id,
            trace_id=trace_id,
            user_context=task_input.user_context,
            text=context.get_user_input(),
            data=command or None,
        )
        await self._publish_result(updater=updater, result=result)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.task_id is None or context.context_id is None:
            raise InvalidParamsError(message="A task ID and context ID are required")
        result = await asyncio.to_thread(
            self.task_service.cancel,
            task_id=context.task_id,
            user_id=resolve_task_owner(context.call_context),
        )
        if result.state is not SubAgentTaskState.CANCELED:
            raise InvalidParamsError(message=result.message)
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=context.task_id,
            context_id=context.context_id,
        )
        await updater.add_artifact(
            parts=[new_data_part(result.data, media_type="application/json")],
            artifact_id=result.artifact_name,
            name=result.artifact_name,
            last_chunk=True,
        )
        await updater.cancel(self._status_message(updater, result))

    @staticmethod
    async def _publish_result(*, updater: TaskUpdater, result: SubAgentTaskResult) -> None:
        await updater.add_artifact(
            parts=[new_data_part(result.data, media_type="application/json")],
            artifact_id=result.artifact_name,
            name=result.artifact_name,
            last_chunk=True,
        )
        message = ActionAgentExecutor._status_message(updater, result)
        if result.state is SubAgentTaskState.INPUT_REQUIRED:
            await updater.requires_input(message)
        elif result.state is SubAgentTaskState.COMPLETED:
            await updater.complete(message)
        elif result.state is SubAgentTaskState.CANCELED:
            await updater.cancel(message)
        elif result.state is SubAgentTaskState.REJECTED:
            await updater.reject(message)
        else:
            await updater.failed(message)

    @staticmethod
    def _status_message(updater: TaskUpdater, result: SubAgentTaskResult):
        return updater.new_agent_message(
            parts=[
                new_data_part(
                    {
                        "state": result.state.value,
                        "message": result.message,
                        **result.data,
                    },
                    media_type="application/json",
                )
            ]
        )


def build_agent_card(*, registry: CapabilityRegistry, public_url: str) -> AgentCard:
    skills = [
        AgentSkill(
            id=f"{entry.capability.application_id}.{entry.capability.id}",
            name=entry.capability.name,
            description=entry.capability.description,
            tags=[
                "action",
                entry.capability.application_id,
                entry.capability.execution.method.value.casefold(),
            ],
            examples=list(entry.capability.discovery_intents[:3]),
            input_modes=["text/plain", "application/json"],
            output_modes=["application/json"],
        )
        for entry in registry.list_latest_active()
    ]
    skills.insert(
        0,
        AgentSkill(
            id="discover_actions",
            name="Discover available actions",
            description="List actions that this Action Agent can perform.",
            tags=["discovery", "actions"],
            examples=["What actions are available?"],
            input_modes=["text/plain"],
            output_modes=["application/json"],
        ),
    )
    return AgentCard(
        name="AI Action Agent",
        description=(
            "Delegated action agent with deterministic parameter collection, authorization, "
            "confirmation, execution, verification, and audit."
        ),
        version=__version__,
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=public_url,
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["application/json"],
        skills=skills,
    )


def install_a2a_adapter(
    app: FastAPI,
    *,
    registry: CapabilityRegistry,
    task_service: SubAgentTaskService,
    public_url: str,
    rpc_path: str = "/",
) -> A2AAdapter:
    """Install the official A2A 1.0 JSON-RPC and Agent Card routes."""
    card = build_agent_card(registry=registry, public_url=public_url)
    executor = ActionAgentExecutor(task_service)
    handler = DelegatedRequestHandler(
        task_service=task_service,
        agent_executor=executor,
        task_store=InMemoryTaskStore(owner_resolver=resolve_task_owner),
        agent_card=card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_path),
    )
    adapter = A2AAdapter(card=card, handler=handler, executor=executor)
    app.state.a2a_adapter = adapter
    return adapter
