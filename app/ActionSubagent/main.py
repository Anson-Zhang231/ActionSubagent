"""AgentCore A2A entrypoint for the existing Action Agent implementation."""

from __future__ import annotations

import os


def main() -> None:
    os.environ.setdefault("HOST", "0.0.0.0")
    os.environ.setdefault("PORT", "9000")
    os.environ.setdefault("A2A_RPC_PATH", "/")
    os.environ.setdefault(
        "A2A_PUBLIC_URL", os.getenv("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/")
    )

    import uvicorn
    from action_runtime.observability import configure_logging

    log_level = os.getenv("LOG_LEVEL", "INFO")
    configure_logging(service_name="action-subagent", log_level=log_level)
    uvicorn.run(
        "action_subagent.api:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "9000")),
        log_level=log_level.lower(),
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
