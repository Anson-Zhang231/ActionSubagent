# Action runtime

This Python package implements the deterministic Action Session lifecycle
used by the A2A SubAgent. It loads the configured application and capability
catalogs; validates input; obtains confirmation;
calls external HTTP APIs; verifies results; and writes audit events. It is
installed together with `action_subagent` through the parent project's
`pyproject.toml`.

The package does not import the A2A adapter or local demo. Configure the main
catalog with `ACTION_CONFIG_DIRECTORY`. An optional second catalog can be added
with `ACTION_EXTRA_CONFIG_DIRECTORY`. A2A uses server-configured
`authorization_mode="supervisor"`: business-user authentication and permissions
are handled upstream, while identity parameter binding and confirmation remain.
`bootstrap` loads local authorization services only when requested by an adapter.
The optional local HTTP adapter uses the default `authorization_mode="local"`;
`AUTHORIZATION_DIRECTORY` and `ACTION_EXTRA_POLICY_DIRECTORY` select its effective
role and policy files.
`MASTER_DATA_DIRECTORY` requires an explicit `MASTER_DATA_FACTORY` provider.

The AI Service integration is in `app/ActionSubagent/tools/ai-service/config/`. The local
demonstration provider and its catalog are in `extras/local-demo/`.
