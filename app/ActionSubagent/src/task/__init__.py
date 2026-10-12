"""Protocol-neutral subagent task contracts and orchestration."""

from .contracts import SubAgentTaskResult, SubAgentTaskState


def __getattr__(name: str):
    if name == "SubAgentTaskService":
        from .service import SubAgentTaskService

        return SubAgentTaskService
    raise AttributeError(name)


__all__ = ["SubAgentTaskResult", "SubAgentTaskService", "SubAgentTaskState"]
