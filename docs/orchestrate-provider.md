# Orchestrate provider notes

See README for own-credential setup and HANDOVER for authority boundaries/known defects.
`llm_providers.py` selects legacy Runtime or Orchestrate. Cloud Toronto authentication
exchanges the instance credential with IBM IAM; cached bearer tokens remain in process.
The client validates agent/environment metadata and uses the supported
`/v1/orchestrate/runs/stream` endpoint, parsing assistant deltas/final messages and run state.
Inspect `orchestrate_client.py` for exact request fields and rejected capabilities.

Fresh remote runs use locally persisted actor-scoped bounded history, not a shared remote
thread. This is application context isolation, not a claim IBM deletes stored run data.
The LIVE agent must have pinned version/model and no remote tools, memory or hooks.
There are no Calendar credentials or hosted Calendar tools in IBM.

Runtime native tool calls are not assumed compatible. `orchestrate_actions.py` requests
strict internal action envelopes. Python rejects unknown operations/identity fields/bad
arguments and allows one bounded format-repair attempt. Read/propose operations return
bounded results to the reasoning loop. Approval/execution stays outside model tools.
Auth, model availability, timeout/rate/quota/stream failures become redacted application
errors. No blind retry of uncertain execution. See offline `test_orchestrate*.py`.

Known real defect: weekly-frequency clarification can still yield an invalid action;
282 historical regressions did not prove every Telegram phrase. Diagnose before demo.
Official references: https://developer.watson-orchestrate.ibm.com/apis/server_openapi.json
and https://developer.watson-orchestrate.ibm.com/environment/initiate_environment .
