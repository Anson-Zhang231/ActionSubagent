# Supervisor integration through A2A

ActionSubagent accepts delegated business identities from a designated trusted
supervisor. Business-user authentication and authorization belong to that
supervisor or its upstream platform. The subagent retains deterministic
validation, identity parameter binding, confirmation, execution, verification
and audit, together with its existing conversation and task ownership checks.
Supervisor and company login integration are not implemented in this repository;
offline tests simulate A2A callers and target API responses.

## Identity and trust

Production uses AgentCore AWS_IAM to authenticate the calling service. The
supervisor must supply user identity from its trusted program layer. End users
do not log into AWS. Restrict runtime invocation IAM permissions to the intended
supervisor service role when deploying; a real supervisor role ARN has not yet
been configured or tested here. The asserted business identity is not an
independently verified company login credential.

`A2A_IDENTITY_MODE=trusted_supervisor` is the supported mode. The runtime does
not use `A2A_DEMO_BEARER_TOKEN` or an A2A bearer-token user map.

`DELEGATED_IDENTITY_CONFIG` selects the identity parameter binding configuration
in `tools/ai-service/config/authorization/delegated-identity.json`. The A2A
entrypoint does not load local business-user/role registries, grant permissions,
evaluate organization or department policies, or reauthorize at confirmation.
The legacy `role_ids` configuration remains compatible but is unused by A2A.
The former AI Service `identities.json` is retained only in
`tests/fixtures/ai-service/authorization/` for legacy authorization tests, and is
excluded from the production configuration. The supervisor must perform its own
checks before delegating and before sending confirmation.

The existing `user_context` is still required for employee mapping, immutable
task/context ownership, and audit correlation. It is asserted metadata rather
than proof of authentication. Request fields `role_ids`, `permission_ids`,
`data_scope` and unknown fields remain rejected. High-risk write confirmation,
parameter binding, and the target API's own workflow checks remain in place.

A2A execution records use `authorization_reason_code: supervisor_managed` and a
`supervisor-managed:<action-id>` lifecycle marker in `authorization_decision_id`.
This marker preserves confirmation/audit linkage and does not claim that the
subagent independently verified an upstream authorization decision. Capability
permission metadata remains compatible. The optional local HTTP demo adapter
continues to load and enforce its local authentication and authorization.
API Basic Auth and AgentCore service IAM authentication still apply.

## A2A request contract

### Machine-readable source of truth

The Python input model
[`DelegatedTaskInput`](src/task/contracts.py) is the only source
of field, type and operation constraints. Its three branches cover text requests,
parameter input and explicit confirmation. The exporter derives
[`a2a-delegated-task-input.v1.0.schema.json`](src/schemas/a2a-delegated-task-input.v1.0.schema.json)
from that model; do not edit the generated schema manually. Markdown describes
the flow and illustrates requests; it does not define another field contract.

The JSON Schema uses Draft 2020-12 and validates the single `parts[].data` object,
not the outer A2A JSON-RPC envelope. The A2A SDK validates that envelope. The
supervisor should use the versioned schema to construct and validate data before
sending it, with format checking enabled and without type coercion or
removal of unknown fields. Service-side checks still enforce task ownership,
existing-task requirements, identity binding and confirmation state.
Schema validation cannot authorize an action or validate a business capability's
`parameters`; those use the registered capability contracts at runtime.

From the repository root, regenerate and check the artifact with:

```powershell
.venv\Scripts\python.exe -m pip install -e app/ActionSubagent
.venv\Scripts\python.exe -m action_subagent.task.export_schema
.venv\Scripts\python.exe -m action_subagent.task.export_schema --check
.venv\Scripts\python.exe -m pytest tests/test_a2a_contract_schema.py -q
```

The exporter supports `--output <path>` for distribution to the supervisor.
`--check` exits nonzero for missing or stale schemas and never overwrites them.
For Python supervisors, `jsonschema.Draft202012Validator(schema,
format_checker=jsonschema.FormatChecker()).validate(data_part)` validates the
data object. Preserve string employee IDs including leading zeros.
The model also preserves accepted compact, `urn:uuid:` and brace-wrapped UUID
inputs through generated pattern constraints; examples use canonical UUIDs.
Employee ID boundary whitespace uses explicit Unicode codepoints so Python and
ECMAScript validators apply the same existing trimming/control-character policy.

`contract_version` identifies this data contract. It is independent of the
`A2A-Version` protocol header, capability versions and the session's
`expected_version`. Currently only `"1.0"` is accepted; unsupported versions
fail before task execution. The 1.0 artifact is frozen in a compatibility test
fixture that the exporter does not update. A future contract change requires an
explicit new version/model/artifact and a decision on retaining the old version;
do not silently rewrite the released fixture. Tests validate every complete
SendMessage example below against both the model and schema, including all three
operation branches, and reject malformed or unsupported-version inputs.

### First request

Local endpoints:

- `GET http://127.0.0.1:9000/.well-known/agent-card.json`
- `POST http://127.0.0.1:9000/`
- `GET http://127.0.0.1:9000/ping`

All RPC requests use `A2A-Version: 1.0` and `Content-Type: application/json`.
AgentCore must forward `A2A-Version` through its `requestHeaderAllowlist`,
alongside `X-Action-User-Id`. Without the version header, the SDK assumes A2A
0.3 and this 1.0 handler rejects the request before capability execution.
The deployment configuration includes both headers; redeploy after changing
the allowlist. Send the version header on every signed cloud RPC request.
Every SendMessage includes one data part with the following identity, including
text-only continuations. IDs below are examples, not real account configuration.

```json
{
  "jsonrpc": "2.0",
  "id": "start-1",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "unique-message-1",
      "role": "ROLE_USER",
      "parts": [
        {"text": "Query my tickets in menu 3"},
        {"data": {
          "contract_version": "1.0",
          "user_context": {
            "user_id": "user-47810",
            "organization_id": "acme",
            "department_id": "engineering",
            "application_user_ids": {"ai_service": "0047810"}
          }
        }}
      ]
    },
    "configuration": {}
  }
}
```

Use the generated schema for identifier and mapping constraints. Only capabilities configured with
identity bindings require their application mapping. Plant/menu need no employee
mapping. The binder injects `username` before schema validation,
parameter hashing and confirmation; callers do not collect that parameter again.
Business HTTP shapes remain those documented in
[the API reference memory](../../memory/ai-service-api.md).

### Text continuation

Use returned IDs and repeat the full original identity. Task history, artifacts,
confirmation summaries and audit payloads preserve original field values,
including employee IDs. Retain the original trusted identity in the supervisor
for continuations. The project does not mask sensitive business fields;
`sensitive_fields` remains descriptive capability metadata, not a masking rule.
Audit `redacted_fields` remains in the response structure and is empty for new events.

```json
{
  "jsonrpc": "2.0",
  "id": "continue-1",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "unique-message-2",
      "role": "ROLE_USER",
      "taskId": "returned-task-id",
      "contextId": "returned-context-id",
      "parts": [
        {"text": "The menu is 3"},
        {"data": {
          "contract_version": "1.0",
          "user_context": {
            "user_id": "user-47810", "organization_id": "acme",
            "department_id": "engineering",
            "application_user_ids": {"ai_service": "0047810"}
          }
        }}
      ]
    },
    "configuration": {}
  }
}
```

For structured parameter input, repeat the full identity and use the returned
task/context IDs. This is a complete request; the version below is illustrative:

```json
{
  "jsonrpc": "2.0",
  "id": "parameters-1",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "unique-message-3",
      "role": "ROLE_USER",
      "taskId": "returned-task-id",
      "contextId": "returned-context-id",
      "parts": [{"data": {
        "contract_version": "1.0",
        "user_context": {
          "user_id": "user-47810", "organization_id": "acme",
          "department_id": "engineering",
          "application_user_ids": {"ai_service": "0047810"}
        },
        "operation": "provide_parameters",
        "expected_version": 12,
        "parameters": {"menu": 3}
      }}]
    },
    "configuration": {}
  }
}
```

Use the current artifact version, not the illustrative value 12. An operator
parameter conflicting with the trusted mapping, or removal of a bound parameter,
is rejected before session/confirmation mutation. For an existing waiting task,
`identity_parameter_conflict` returns INPUT_REQUIRED and the original confirmation
remains usable. Missing required employee mapping returns
`identity_mapping_required`. Identity changes require a new task; task/context
bindings are immutable for user, organization, department and application mapping.

### Explicit confirmation

When the artifact has `input_kind: confirmation`, present its summary to the
user. After their explicit decision, use SendMessage with the same IDs and full
identity in the data part. This complete example uses an illustrative UUID/version:

```json
{
  "jsonrpc": "2.0",
  "id": "confirmation-1",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "unique-message-4",
      "role": "ROLE_USER",
      "taskId": "returned-task-id",
      "contextId": "returned-context-id",
      "parts": [{"data": {
        "contract_version": "1.0",
        "user_context": {
          "user_id": "user-47810", "organization_id": "acme",
          "department_id": "engineering",
          "application_user_ids": {"ai_service": "0047810"}
        },
        "operation": "confirmation",
        "decision": "confirm",
        "confirmation_id": "8bd3f58a-96f3-4992-a805-d67844ddcd60",
        "expected_version": 18
      }}]
    },
    "configuration": {}
  }
}
```

Replace the UUID and version with current artifact values. `decision: cancel`
declines the pending action. The supervisor must not infer confirmation from
its reasoning or the initial request. Positive text such as `yes` cannot confirm
execution. Missing, stale, expired or replayed confirmation IDs/versions cannot
repeat a write. Each new action uses a new task.

### Independent task access

GetTask, CancelTask, ListTasks and SubscribeToTask require
`X-Action-User-Id: user-47810`. SendMessage may omit this header; when supplied it
must equal data.user_context.user_id. This header is a trusted caller assertion.
AgentCore `requestHeaderAllowlist` forwards it to the application.

```json
{"jsonrpc":"2.0","id":"get-1","method":"GetTask","params":{"id":"returned-task-id"}}
```

```json
{"jsonrpc":"2.0","id":"cancel-1","method":"CancelTask","params":{"id":"returned-task-id"}}
```

```json
{"jsonrpc":"2.0","id":"subscribe-1","method":"SubscribeToTask","params":{"id":"returned-task-id"}}
```

```json
{"jsonrpc":"2.0","id":"list-1","method":"ListTasks","params":{"pageSize":10}}
```

Subscription responses use SSE. Lists are owner-filtered before pagination;
unknown and other-user task IDs share the same `Task is unavailable` error, and
terminal tasks remain private. Independent cancellation currently requires a
session awaiting confirmation. Agent Card and ping retain their existing access
behavior; unsupported push notification methods remain unsupported.

`SendStreamingMessage` accepts the same message and identity envelope as
SendMessage and returns SSE events. Structured continuation operations require
an existing task ID; they cannot start a new task.

### Error handling

| Condition | Result and supervisor action |
| --- | --- |
| Invalid data shape, unsupported contract version, malformed operation or mismatched user header | JSON-RPC InvalidParams (`-32602`); correct the request using the schema before retrying. |
| Unknown task, other user's task or changed bound identity | TaskNotFound (`-32001`), `Task is unavailable`; use the original identity and returned IDs. |
| Missing required application identity | Action artifact code `identity_mapping_required`; supply trusted mapping when starting a new task. |
| Model/parameter conflicts with bound operator | Action artifact code `identity_parameter_conflict`; correct parameters; the existing waiting session/confirmation remains intact. |
| Stale session or confirmation version | Action artifact code `task_version_conflict`; get current state and obtain a new explicit decision if needed. |
| Business-user permission denied upstream | Supervisor rejects delegation or confirmation; local role changes do not authorize or revoke A2A execution. |

Action artifact codes are returned within task results, separately from JSON-RPC
errors. Format/schema validity does not guarantee that an action will be allowed.

## Offline verification and local start

```powershell
.venv\Scripts\python.exe -m pytest -q
$env:A2A_IDENTITY_MODE = "trusted_supervisor"
.venv\Scripts\python.exe app/ActionSubagent/main.py
```

Model selection requires the existing interpreter/provider settings; tests use
fake interpreters and connectors. Production A2A can start without an HTTP demo
token map. The optional HTTP demo adapter still authenticates its bearer tokens
when used. Offline checks do not verify a real supervisor, company SSO, deployed
IAM role, gateway credentials or live E-Ticket business permissions.

### Batch checks against the real API

In the server terminal, set proxy exclusions before restarting the diagnostic
server. This selects the production gateway; the script prompts for API Basic
Auth if it is not already present in the process environment:

```powershell
$env:NO_PROXY = 'api.svi.co.th,api-test.svi.co.th,localhost,127.0.0.1'
.\.superpowers\start-create-diagnostic.ps1 -ApiBaseUrl 'https://api.svi.co.th' -ApiTimeoutSeconds 60
```

In a second terminal, run the batch client:

```powershell
.\.superpowers\test-live-capabilities.ps1 -NoProxy -EmployeeId '47810' -Plant 'SVI2' -MenuId 1 -TicketNo '260918001'
```

The client tests all seven AI Service capabilities through A2A. `-NoProxy`
passes `--noproxy '*'` to curl for client requests; upstream API proxy bypass
comes from the server process's `NO_PROXY`. Keep proxy exclusions specific to
these hosts so the existing Bedrock connection settings continue to apply.

The writes read `.superpowers/ticket-create-test-data.json` and
`.superpowers/ticket-status-test-data.json`; optional `-CreateDataPath` and
`-StatusDataPath` override them. Review these files before a run. The status
test uses its own supplied ticket number and employee, independently of the
read-side `-TicketNo` and newly created ticket. Writes require an exact input
match and an explicit CREATE or status-name confirmation.

Each task and `summary.json` are saved under a timestamped
`.superpowers/live-capabilities-*` directory. PASSED means the subagent returned
a successful verified API response for the requested parameters; it is not an
independent database or ticket-history readback. For status history readback,
use the existing `.superpowers/ticket-status.ps1` diagnostic. Exit codes are
0 for all passed, 1 for any failed check, and 2 for canceled writes without failures.
The batch does not automatically retry writes after transport failures.

`-ApiTimeoutSeconds` sets the upstream socket timeout in a generated local
capability copy for the diagnostic server; it leaves versioned source files
unchanged and defaults to their existing 10 seconds. This timeout is distinct
from the client's 180-second curl timeout and from Bedrock intent interpretation.
Use `-ReadOnly` on the batch client to retest only the five query capabilities
after an uncertain write, preventing a repeated create or status update.
`UPSTREAM_FAILURE` logs include the trace ID, exception class, request phase,
configured timeout and elapsed time for transport failures. A timed-out write
remains indeterminate until a readback confirms its actual effect.

### Full live workflow regression

To retest the seven capabilities and all six standard status actions, keep the
diagnostic server running with the 60-second timeout and run:

```powershell
.\.superpowers\test-all-live-workflows.ps1 -NoProxy -ReassignEmployeeId '47813'
```

The suite takes the creator, menu, subcategory, approver and initial operator
from `.superpowers/ticket-create-test-data.json` (override with
`-CreationDataPath`). It validates these employees and subcategory against fresh
creation options, generates unique titles and future due/commitment dates, and
creates two independent test tickets:

- Complete branch: Create, Approve, Accept, Extend, Assign, Complete. If assignment
  returns a status requiring acceptance, the new operator also confirms Accept.
- Reject branch: Create, then Reject while awaiting approval.

Each write has its own explicit confirmation. Actors are taken from current
ticket headers: approver for Approve/Reject, assigned operator for the other
actions. `-ReassignEmployeeId` selects the new operator. All detail readbacks use
the creator, preserving read access after reassignment. A status action passes
only after a new matching history entry and expected header changes are observed;
an uncertain write is reconciled through reads and is never automatically
resubmitted. Creation requires the returned ticket ID and a matching header.
An unknown creation outcome stops that branch for manual inspection.

Readback expects `Waiting Operator` after Approve, `On Process` after Standard
Accept/Extend, `Complete` after Complete, and `Reject` after Reject. Assignment
must change the operator and leave `On Process` or `Waiting Operator` (the latter
requires acceptance by the new operator). These labels use observed live ticket
data; a different workflow state fails verification for inspection even when
the API returns success and adds history.

The live API decides which actions are permitted. The supplied AI Service
specification does not define the complete state transition rules; the suite
stops dependent actions when a step fails, and records remaining steps as
NOT_RUN. A cancellation stops the whole suite. The independent Reject branch
can still be tested after a failed Complete branch. UAT/Head actions are outside
this six-action suite.

Results and numbered request/response tasks are saved under
`.superpowers/live-full-*`. `summary.json` contains every planned check, including
unexecuted checks. Exit codes are 0 when all checks passed, 1 on failure or an
unexecuted check, and 2 after user cancellation without other failures.

## AgentCore Runtime packaging

### Project naming and deployment migration

The AgentCore project name and Project tag are `ActionSubagent`. The runtime
entry in `agentcore/agentcore.json` is named `Runtime`. The current
CDK library combines project and runtime names, producing the physical Runtime
name `ActionSubagent_Runtime` and, for the default target, the stack
`AgentCore-ActionSubagent-default`.
AWS Runtime names accept letters, digits and underscores, so the requested
display spelling `ActionSubagent-Runtime` uses an underscore in the physical
AWS name. CLI resource commands select it with `--runtime Runtime`.

Changing the project name changes the stack and resource names. Deployment
creates a new stack; it does not rename the previously deployed stack in place.
The previous stack can remain active and incur charges. Inspect
`agentcore deploy --dry-run` before deploying, then verify the new Runtime and
switch callers to its actual ARN. Retire the old stack separately after the new
deployment has been verified. Changing an existing Runtime's physical name also
requires replacement under CloudFormation.

Do not search-and-replace actual Runtime ARNs, CLI deployment state, or captured
historical responses. `.superpowers/invoke-agentcore.py` obtains the Runtime ARN
from the `Runtime` entry in `agentcore/.cli/deployed-state.json` for the default
target, falling back to the previous `ActionSubagent` entry; until a new
deployment updates that state, it continues to address the existing Runtime.
`AGENTCORE_RUNTIME_ARN` can explicitly select an actual ARN and takes precedence
over deployment state. The cloud caller automatically uses a new state ARN after
deployment. Historical CDK reports preserve the original resource names.

PyCharm's project display name and root module are `ActionSubagent`. The requested
checkout folder is `MySubagent`, independently of the AgentCore project name.
After moving the directory, point PyCharm at `MySubagent/.venv/Scripts/python.exe`
and update the saved Codex project path. Existing generated deployment IDs and
captured responses retain the actual AWS resource identities.

### Container and configuration

`app/ActionSubagent/main.py` is the runtime entrypoint. The
`agentcore/agentcore.json` uses `app/ActionSubagent/` as its build context and
`Dockerfile.agentcore` as its Dockerfile. The Dockerfile
copies the SubAgent, the shared Action runtime, and the AI Service integration
catalog. It does not copy `extras/local-demo/`. The production image omits
the HTTP API modules and its A2A app exposes no HTTP action routes. The
production Agent Card therefore advertises `ai_service.plant`, `ai_service.menu`,
`ai_service.ticket_context`, `ai_service.ticket_create`, `ai_service.ticket_detail`, `ai_service.ticket_status`, and
`ai_service.ticketlist` (plus discovery). It listens on
`0.0.0.0:9000`, serves JSON-RPC at `/`, Agent Card at
`/.well-known/agent-card.json`, and health at `/ping`. Build an ARM64 image:

```powershell
docker buildx build --platform linux/arm64 -f app/ActionSubagent/Dockerfile.agentcore -t <ecr-image-uri> --push app/ActionSubagent
```

The AgentCore CLI layout is ready for a deployment target. Verify the AWS account
and Region in `agentcore/aws-targets.json` before deploying. Copy the shape from
`agentcore/aws-targets.example.json` and replace its example account ID with
your real 12-digit AWS account ID. The example file is not read by the CLI:

```json
[
  {
    "name": "dev",
    "description": "Development account",
    "account": "YOUR_12_DIGIT_ACCOUNT_ID",
    "region": "ap-southeast-1"
  }
]
```

Replace the placeholder, then use `agentcore deploy --dry-run` before deploying.
`agentcore/agentcore.json` is the CLI-managed resource inventory: it declares
one code-based A2A Runtime. Its memory, gateway, credential, and harness arrays
are empty because this wrapper does not provision those AgentCore resources.
Use `agentcore add` or `agentcore remove` to manage them when needed; do not add
dummy resources merely to fill the arrays. The custom CDK app in
`agentcore/cdk/` reads this file and the selected AWS targets.

For local settings, copy the needed values from `agentcore/.env.local.example`
into the gitignored `agentcore/.env.local`. The latter currently contains
comments and no secrets. Bedrock access uses the AWS credential chain;
do not put AWS access keys in the file. AgentCore credentials, if added later,
must be configured according to the CLI credential provider flow.

Configure `A2A_PUBLIC_URL` to the externally reachable Runtime A2A invocation
URL; the Agent Card advertises this value to supervisors. Set `BEDROCK_REGION`
and `BEDROCK_MODEL_ID`, and grant the Runtime execution role model invoke access.

The demo Application definitions use Compose hostnames
(`demo-service-desk` and `demo-leave-system`); these are not copied into the
default AgentCore image. The application and capability files copied into the
image select the AI Service gateway and HTTP timeouts. Diagnostic launcher
overrides are local and are not packaged. Offline gateway tests use temporary
configurations for both test and production addresses; launcher tests verify
timeout overrides and byte-for-byte preservation of source configurations.
Action Session, A2A Task, conversation and audit records remain in memory.
AgentCore Runtime session affinity does not make these records durable. Restarting
the process loses task ownership and pending confirmations; horizontal scaling
requires routing affinity or a future shared store. Live gateway and supervisor behavior
must be verified with the actual deployment before production use.
