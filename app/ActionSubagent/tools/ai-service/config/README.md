# AI Service integration catalog

This directory contains the external SVI AI Service E-Ticket integration used
by the A2A SubAgent. `applications/` selects the test gateway and a secret
reference; `capabilities/` contains seven versioned actions; and
`authorization/` contains policies plus the server-side delegated role
configuration.

The generic Action runtime loads this catalog from `ACTION_CONFIG_DIRECTORY`.
The AgentCore image copies it to `/app/config`. The local demo adds separate
registries from `extras/local-demo/config` using
`ACTION_EXTRA_CONFIG_DIRECTORY`; those files are not part of this integration.

The configured host is `https://api-test.svi.co.th`. The supplied
[AI Service reference](../../../memory/ai-service-api.md) documents the gateway
contract but does not prove the configured `/ai/service/` route or response
shape against a live service. Keep credentials in a runtime secret source.
The default delegated reader role cannot create or update tickets; write
permissions and user identity mapping require explicit deployment setup.
