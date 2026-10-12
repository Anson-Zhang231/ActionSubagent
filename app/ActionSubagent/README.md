# ActionSubagent

This package owns the supervisor-facing A2A 1.0 endpoint and the delegated
SubAgent Task lifecycle. Its source package contains only `api.py`, `a2a/`,
`task/`, and the versioned A2A input schema. The deterministic Action Session
runtime lives in `action_runtime/`. API capability and connection configuration
lives in `tools/ai-service/config/`. Both Python packages are installed through
this project's single `pyproject.toml`.

The supervisor or upstream platform owns business-user authentication and
authorization. The A2A entrypoint does not load local user/role grants. Existing
employee parameter binding, task ownership, conversation handling, explicit
write confirmation, response verification and audit remain enabled. Service
credentials for the target API and AgentCore IAM access are still required.

The physical `src/` directory is mapped to the Python import name
`action_subagent`; its subpackages remain `action_subagent.a2a` and
`action_subagent.task`. This keeps the adapter distinct from the external
`a2a` SDK. Use an editable installation for source development instead of
adding `src/` directly to `PYTHONPATH`.
In PyCharm, mark `app/ActionSubagent/` as the source root. Internal imports
use relative paths so the IDE can resolve the physical `src/` package while
the installed package keeps the import name `action_subagent`.

`main.py` starts the A2A service on port 9000. The Agent Card is served at
`/.well-known/agent-card.json`, JSON-RPC at `/`, and health at `/ping`.
The production image uses `Dockerfile.agentcore` with `app/ActionSubagent/` as
its build context. It includes the AI Service catalog from
`app/ActionSubagent/tools/ai-service/config/` and excludes the local demo.

Run from the repository root after installing the project:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .\app\ActionSubagent
.\.venv\Scripts\python.exe .\app\ActionSubagent\main.py
```

See [AGENTCORE.md](AGENTCORE.md) for the supervisor contract, identity trust
boundary, configuration, and deployment details.

The AI Service catalog currently targets a test gateway. A live API call has
not verified the configured path or response shape. Runtime credentials and
business identity mappings must be provided outside committed files.
