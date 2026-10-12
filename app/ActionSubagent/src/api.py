"""A2A application built on the unified ActionSubagent services."""

from __future__ import annotations

import os

from action_runtime.bootstrap import (
    application_registry,
    build_delegated_identity_resolver,
    discovery_service,
    identity_parameter_binder,
    intent_service,
    master_data_validation_service,
    registry,
)
from action_runtime.observability import DEFAULT_TRACE_ID_HEADER, TraceIdMiddleware
from action_runtime.services.action_execution import ActionExecutionService
from fastapi import FastAPI

from . import __version__
from .a2a import install_a2a_adapter
from .task import SubAgentTaskService

app = FastAPI(
    title="AI Action Subagent",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    TraceIdMiddleware,
    service_name="action-subagent",
    header_name=os.getenv("TRACE_ID_HEADER", DEFAULT_TRACE_ID_HEADER),
)


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "Healthy"}


delegated_identity_resolver = build_delegated_identity_resolver(enforce_authorization=False)
identity_parameter_binder.validate_rules(delegated_identity_resolver.binding_rules())
sessions = ActionExecutionService(
    registry,
    application_registry=application_registry,
    connector_simulation_header=os.getenv("CONNECTOR_SIMULATION_HEADER"),
    master_data_validator=master_data_validation_service,
    identity_parameter_binder=identity_parameter_binder,
    authorization_mode="supervisor",
)
subagent_task_service = SubAgentTaskService(
    registry=registry,
    intent_service=intent_service,
    discovery_service=discovery_service,
    sessions=sessions,
    identity_resolver=delegated_identity_resolver,
)
a2a_adapter = install_a2a_adapter(
    app,
    registry=registry,
    task_service=subagent_task_service,
    public_url=os.getenv("A2A_PUBLIC_URL", "http://127.0.0.1:9000/"),
    rpc_path=os.getenv("A2A_RPC_PATH", "/"),
)
