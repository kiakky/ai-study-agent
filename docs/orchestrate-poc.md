# Orchestrate feasibility checkpoint — 14 October 2026

Python authentication, LIVE deployment metadata access, LIVE run execution and
complete assistant reply extraction/display are **live-verified**. The final
user-authorized check used one inference submission, four HTTP calls and zero
retries. No production integration was changed. This isolated POC is ready for a
local commit and the separately reviewed provider-adapter milestone.

## Evidence and limitation

The active source repository is the project root on `main`, initially clean at
`2afff58`. Its README, HANDOVER and backend match the deployed two-tester application.
Registered `.worktrees` are development branches, not the running root. No separate
sanitized publication snapshot was found within the inspected project; no external
snapshot was substituted for the application source.

The three WXO settings were consumed selectively from the private project `.env`
or existing process environment. No other credential configuration was loaded.
The URL hostname is IBM Cloud Toronto, so IBM IAM is appropriate. AWS MCSP keys
have different semantics; the POC intentionally does not guess or fall back.

The first check authenticated and verified deployment metadata but stopped before
inference because an empty plugin-default object was mistakenly considered active.
Read-only diagnosis and the official Plugins schema established that empty
pre/post-invoke hooks are inert; the guard was corrected. The release has no tools,
collaborators, toolkits, knowledge bases or enabled memory.

Two subsequent synthetic runs returned HTTP 200. The final stream contained
`run.started`, `message.started`, `run.step.intermediate`, `message.delta`,
`message.created`, `message.completed`, `run.completed`, and `done`.
The `message.completed` notification contained only run/thread/trace identifiers
and usage metadata, not assistant content. The original parser therefore failed
to display the reply despite the completed run. No raw response, trace, token,
thread identifier or student data was saved.

The revised parser supports text deltas and assistant-message snapshots and requires
`run.completed`. Ten synthetic tests pass, including metadata-only completion,
redaction, LIVE selection, zero retries, timeouts, configuration validation and
tool/memory blocking and complete Unicode CLI output. The final live verification
with the revised parser exited successfully and displayed this complete sentence:

> I’m an AI study assistant here to help you understand subjects, prepare for exams, and create realistic study schedules.

The verified LIVE release reported model `groq/openai/gpt-oss-120b`, semantic
version `1.0.0`. The initial Windows console output garbled the curly apostrophe;
the POC now explicitly emits UTF-8, verified offline without another inference.
No additional live calls were made after the successful verification.

Credentialed investigation total across both turns: 17 HTTP requests, including 3
synthetic inference submissions and 5 IAM exchanges. No HTTP authentication, access, rate-limit or quota
error was observed. Remaining quota and provider token accounting are unknown.

## Documented flow

1. `POST https://iam.cloud.ibm.com/identity/token`, form encoded with
   `grant_type=urn:ibm:params:oauth:grant-type:apikey` and the WXO API key.
   Retain `access_token` in process memory only.
2. Use `Authorization: Bearer <access_token>` for instance-relative API calls.
3. `GET {WXO_API_URL}/v1/orchestrate/agents/{WXO_AGENT_ID}/environment` selects
   the existing `live` environment and its current integer version.
4. `GET {WXO_API_URL}/v1/orchestrate/agents/{WXO_AGENT_ID}/releases` checks the
   corresponding semantic version `1.0.0` and verifies no execution capabilities.
5. `POST {WXO_API_URL}/v1/orchestrate/runs/stream?stream_timeout=45000` with:

```json
{
  "agent_id": "<configured agent>",
  "environment_id": "<existing LIVE environment>",
  "version": 1,
  "message": {
    "role": "user",
    "content": "Hello, introduce yourself in one sentence."
  }
}
```

The actual integer version comes from LIVE metadata; it is not hardcoded to 1.
The request creates a fresh synthetic provider conversation/run. It does not
reuse student threads, create agents, import tools or redeploy anything.

The simpler documented `/v1/orchestrate/{agent_id}/chat/completions` endpoint has
no explicit LIVE environment selector in its published ChatCompletion schema.
The run endpoint was chosen to make LIVE selection explicit and verifiable.
No watsonx.ai Runtime endpoint, project ID or Runtime API-version parameter is used.
No model token cap is assumed to be enforced by this agent run API; the short prompt
and single submission limit inference calls, not a guaranteed token bill.

Sources read:

- [IBM environment credentials](https://developer.watson-orchestrate.ibm.com/environment/production_import)
- [IBM authentication/environment types](https://developer.watson-orchestrate.ibm.com/environment/initiate_environment)
- [IBM official OpenAPI specification](https://developer.watson-orchestrate.ibm.com/apis/server_openapi.json)
- [IBM agent chat API](https://developer.watson-orchestrate.ibm.com/apis/orchestrate-agent/chat-with-agents)
- [IBM deployment states](https://developer.watson-orchestrate.ibm.com/agents/import_agent)
- Public IBM ADK `ibm-watsonx-orchestrate` 2.18.1, inspected in memory without
  installation: `cli/commands/server/types.py` independently confirms that
  `.cloud.ibm.com` uses IBM IAM rather than MCSP.

## Local use and next action

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_orchestrate_poc.py -v
.venv\Scripts\python.exe scripts\check_orchestrate.py
# Optional future explicit verification; consumes one inference, not needed now
.venv\Scripts\python.exe scripts\check_orchestrate.py --verify
```

Without `--verify`, the script makes no API calls and reads no `.env` settings.
Retries are deliberately bounded at zero. A lost/failed stream can represent an
already completed billable run; do not resend automatically. HTTP failures and raw
exceptions are redacted. Unknown API shapes fail visibly rather than trying guessed
endpoints or authentication types. The POC imports no bot, API, database, OAuth or
LMS modules. Tests use mocked HTTP and temporary synthetic configuration only.

The conversational connectivity milestone is complete for this deployed agent and
model. The next milestone is a separately authorized provider adapter, including
synthetic compatibility checks before any production rollout. No Telegram/Mini App
manual testing is needed for this POC. Multi-turn history, concurrent user isolation,
native tool calls and approval flows have not been verified against Orchestrate.

## Future provider replacement (not implemented)

The existing `services/llm_contracts.py` exposes `ChatProvider.complete(...)` and
`ChatReply`, including validated tool calls. Add an Orchestrate adapter plus a
provider factory at the existing construction point in `conversation_telegram.py`;
keep the current provider available through explicit configuration. Add in-memory
token expiry/refresh and existing redacted error codes. Do not run this POC directly
as the production provider.

Conversation-only use needs authenticated per-user context boundaries and ownership
checks for any provider thread. Start fresh threads or map provider threads strictly
to server-verified Telegram users and conversation generations; never share a thread
across accounts. Clearing history must invalidate that mapping. Raw Telegram IDs,
initData and OAuth tokens must not be sent as model-authoritative identity claims.
Provider-side agentic memory is disabled in this POC and must be assessed explicitly
before any multi-user rollout.

The current CalendarAgent requires native tool definitions, structured calls and
tool-result continuation. Greeting connectivity does not establish that the agent
run API can implement that contract. Prove this separately with mocks/synthetic
tools before changing production orchestration. Keep actor-bound allowlists,
schema validation, queues, message tombstones, leases, exact-action digests,
expiration checks, explicit Telegram approvals and Calendar executors in Python.
No LLM-supplied user ID or agent-generated approval can authorize execution.

Keep Brightspace retrieval read-only through the existing LMS allowlist; do not
expose its generic MCP call helper as an agent tool. A future integration must also
add `WXO_` to the Brightspace subprocess secret-exclusion prefixes: the current
filter covers older providers but not these new settings. No Brightspace process
was launched or accessed during this investigation, and no production code was
changed to address that future integration requirement.
