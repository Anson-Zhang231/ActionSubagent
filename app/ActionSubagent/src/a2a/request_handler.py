from __future__ import annotations

from a2a.helpers.proto_helpers import get_data_parts
from a2a.server.agent_execution import SimpleRequestContextBuilder
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.utils.errors import InvalidParamsError, TaskNotFoundError
from action_runtime.domain.delegation import Identifier
from action_runtime.services.delegated_identity import DelegatedIdentityError
from pydantic import TypeAdapter, ValidationError

from ..task.contracts import DelegatedTaskInput
from ..task.service import SubAgentTaskService, TaskAccessError


def resolve_task_owner(context: ServerCallContext) -> str:
    user_id = context.state.get("action_user_id")
    if not isinstance(user_id, str) or not user_id:
        raise InvalidParamsError(message="A delegated user identity is required")
    return user_id


class DelegatedRequestContextBuilder(SimpleRequestContextBuilder):
    def __init__(self, task_service: SubAgentTaskService) -> None:
        super().__init__()
        self.task_service = task_service

    async def build(self, context, params=None, task_id=None, context_id=None, task=None):
        result = await super().build(context, params, task_id, context_id, task)
        if params is not None:
            task_input = context.state["action_task_input"]
            try:
                self.task_service.bind_task(
                    task_id=result.task_id,
                    context_id=result.context_id,
                    user_context=task_input.user_context,
                )
            except TaskAccessError as error:
                raise TaskNotFoundError(message="Task is unavailable") from error
        return result


class DelegatedRequestHandler(DefaultRequestHandler):
    """Guard before SDK Task Store and unscoped active-task cache access."""

    def __init__(self, *, task_service: SubAgentTaskService, **kwargs) -> None:
        self.task_service = task_service
        super().__init__(
            request_context_builder=DelegatedRequestContextBuilder(task_service), **kwargs
        )

    @staticmethod
    def _header(context: ServerCallContext, *, required: bool) -> str | None:
        headers = context.state.get("headers", {})
        value = next((v for k, v in headers.items() if k.casefold() == "x-action-user-id"), None)
        if value is None and not required:
            return None
        try:
            return TypeAdapter(Identifier).validate_python(value)
        except ValidationError as error:
            raise InvalidParamsError(message="A valid X-Action-User-Id is required") from error

    def _prepare_message(self, params, context: ServerCallContext) -> None:
        parts = get_data_parts(params.message.parts)
        if len(parts) != 1 or not isinstance(parts[0], dict):
            raise InvalidParamsError(message="One structured delegated identity object is required")
        try:
            task_input = DelegatedTaskInput.model_validate(parts[0])
            self.task_service.identity_resolver.resolve(task_input.user_context)
        except (ValidationError, DelegatedIdentityError) as error:
            raise InvalidParamsError(
                message="Delegated identity or task input is invalid"
            ) from error
        user_id = task_input.user_context.user_id
        if task_input.operation is not None and not params.message.task_id:
            raise InvalidParamsError(message="A structured continuation requires an existing task")
        header = self._header(context, required=False)
        if header is not None and header != user_id:
            raise InvalidParamsError(message="Delegated user header does not match user_context")
        try:
            if params.message.task_id:
                if not params.message.context_id:
                    raise TaskAccessError()
                self.task_service.assert_task_access(
                    task_id=params.message.task_id,
                    context_id=params.message.context_id,
                    user_id=user_id,
                    user_context=task_input.user_context,
                )
            elif params.message.context_id:
                self.task_service.assert_context_access(
                    context_id=params.message.context_id,
                    user_context=task_input.user_context,
                )
        except TaskAccessError as error:
            raise TaskNotFoundError(message="Task is unavailable") from error
        context.state["action_user_id"] = user_id
        context.state["action_task_input"] = task_input

    def _prepare_access(self, task_id: str | None, context: ServerCallContext) -> None:
        user_id = self._header(context, required=True)
        context.state["action_user_id"] = user_id
        if task_id is not None:
            try:
                self.task_service.assert_task_access(task_id=task_id, user_id=user_id)
            except TaskAccessError as error:
                raise TaskNotFoundError(message="Task is unavailable") from error

    async def on_message_send(self, params, context):
        self._prepare_message(params, context)
        return await super().on_message_send(params, context)

    async def on_message_send_stream(self, params, context):
        self._prepare_message(params, context)
        async for event in super().on_message_send_stream(params, context):
            yield event

    async def on_get_task(self, params, context):
        self._prepare_access(params.id, context)
        return await super().on_get_task(params, context)

    async def on_list_tasks(self, params, context):
        self._prepare_access(None, context)
        return await super().on_list_tasks(params, context)

    async def on_cancel_task(self, params, context):
        self._prepare_access(params.id, context)
        return await super().on_cancel_task(params, context)

    async def on_subscribe_to_task(self, params, context):
        self._prepare_access(params.id, context)
        async for event in super().on_subscribe_to_task(params, context):
            yield event
