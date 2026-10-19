# Native Calendar tools — milestone 1, deployment pending

Implemented on isolated branch `calendar-agent-tools`, based on deployed `47092b3`.
**Not deployed or pushed.** The original root, running processes, private configuration,
production database, Google OAuth setup and sanitized publication checkout were not changed.

## Feasibility established

IBM's documented model gateway supports native function definitions, model-selected
`tool_calls`, and paired tool-result continuation:

`POST /v1/orchestrate/gateway/model/chat/completions`

The initial harmless live probe returned a native synthetic tool call and then a natural
answer after receiving its synthetic result (two submissions). Subsequent tests exercised
real GPT-OSS 120B reasoning through this endpoint with temporary SQLite, synthetic actors
and mocked Google. No callback server, remote tool registration, remote agent modification,
agent deployment or new dependency was needed.

The reasoning model remains `groq/openai/gpt-oss-120b`, accessed through the existing IBM
instance and IAM service/library. The new path uses IBM Orchestrate's model gateway;
the existing LIVE agent Run API is not used for this native loop. Its remote deployment
is unchanged. There is no automatic provider or JSON-prompt fallback on failure.

Official schema inspected: https://developer.watson-orchestrate.ibm.com/apis/server_openapi.json

## Model-directed interaction

The Orchestrate factory now selects `OrchestrateNativeProvider`. Telegram selects
`NativeCalendarAgent` for that provider. The existing watsonx provider/agent remain available.

The native loop exposes five operations:

- `calendar_context`: refresh own settings, connection and session references, with filtering/pagination.
- `find_calendar_slots`: check availability for model-selected dates, days, windows and duration.
- `create_calendar_events`: prepare one or many exact events from returned slots. Repeated calls
  append to the current turn's proposal. Adding to an older draft and replacing a complete
  draft have explicit operation flags; selected amendments use the change tool.
- `change_calendar_event`: prepare an owned pending/scheduled session move or cancellation.
- `propose_preferences`: offer an explicitly requested permanent settings change for human review.

There is no natural-language routing classifier, frequency regex or forced planning sequence
on this path. The model interprets free wording, follow-ups, workload and timing, chooses
tools, and continues until it has a response or proposal. Python supplies factual actor-scoped
settings, existing state and calendar arithmetic so the model can reason from current data.
These facts do not select an intent or parse a student's sentence. The model can query
multiple ranges/windows and select its own number/distribution of checked slots.

## Approval and isolation

The model-visible create/change tools stage proposals; they cannot approve or directly
execute a Google write. Existing exact Telegram review/buttons and human-decision handlers
remain authoritative. Proposal updates invalidate previous reviews, and only the latest
complete version is presented for a message. Backend checks still enforce actor ownership,
connection generation, availability/conflicts, request idempotency, expiry and stale controls.

Tool arguments contain no authenticated actor ID. References resolve only against the
authenticated actor's context and checked availability. Unknown/identity arguments fail
closed. The model can correct ordinary typed argument errors from tool feedback. Identical
calls without a state change are stopped; legitimate context refreshes after a change work.
Each request is bounded to eight model turns, sixteen tool operations and the existing
conversation timeout/fences. Network-uncertain inference is never replayed automatically.

Private notes, OAuth/account fields, Google event IDs and credentials are stripped from
outbound tool-result continuations. Model prose cannot authorize writes; successful proposal
turns return a backend confirmation and the existing UI renders the exact stored schedule.

## Tests and findings

The implementation was tested incrementally rather than treating initial native support
as proof of complete Calendar behavior. Native gateway tests exposed nested-schema field
confusion; inlining JSON Schema references made argument structures explicit. Synthetic
tests also exposed weekday arithmetic/default-window mistakes. Authoritative calendar dates,
current saved/pending state, clear override semantics and medium model reasoning resolved
the exercised cases. An early schema-helper bug dropping the actual `title` property was
fixed and covered by a regression. An early test-console Unicode error was also fixed.

Final live synthetic scenarios passed:

- Exact original broad request → `I think 2-3 sessions per week should be enough` → `2`:
  four 30-minute sessions, two per week; selected first-session amendment, preserved other
  three sessions, invalid old review and no-write rejection (seven model submissions).
- Tuesday/Thursday/Saturday afternoons, twenty minutes, next week: three correctly timed
  sessions, unchanged saved defaults, selected amendment and rejection (five submissions).
- Move only the second scheduled session: one owned move proposal, no execution (two submissions).
- Cancel only the second scheduled session: one owned cancellation proposal, no execution (two submissions).

Full offline regression run: **313 tests passed, zero failures/errors/skips**. After adding
the final real Telegram-handler/dispatcher integration test, the targeted native suite
passed **23 tests**, including that additional test. Coverage includes native protocol and
continuation, original free wording through persisted history, repeated creation calls,
exact human approval/idempotency, selected amendments, pending cancellation, concurrent
actors, stale/forged references, conflict changes, disconnected state, outbound privacy,
loop bounds and mocked Telegram presentation. The integration test verifies the actual
dispatcher chooses the native agent, renders the four-session approval, and rejects an
impostor chat. Telegram/Google transport remains mocked in all automated scenarios.

No production student records were inspected, no real Calendar events or Telegram messages
were sent, and no Brightspace operations occurred. No schema migration is required.

## Reproduction and deployment boundary

From this isolated worktree:

```powershell
..\..\.venv\Scripts\python.exe -m unittest discover -s tests
..\..\.venv\Scripts\python.exe scripts/check_orchestrate_native.py
..\..\.venv\Scripts\python.exe scripts/check_native_calendar.py
# Explicit opt-in real IBM inference; Google/Telegram stay mocked:
..\..\.venv\Scripts\python.exe scripts/check_native_calendar.py --verify --scenario dialogue --env-file ..\..\.env
```

The harnesses access no credentials/services without `--verify`. Each live scenario has
a bounded submission budget and stops on failure; diagnostic output is explicitly synthetic.

This is ready for deployment review, not a claim of real Telegram acceptance. Model adherence
is not guaranteed for every phrasing, and provider schema/network errors still fail safely.
A fresh user test after approved deployment must verify own-account proposal, amendment,
explicit approval, actual Google result, and individually approved move/cancel operations.

Safe public conversation onboarding is unchanged: two configured testers remain the audience.
Milestone 2 has not begun. Deployment requires separate approval; do not merge or restart
the live root merely to run these tests. Keep private local history separate from the
sanitized GitHub snapshot when later publishing reviewed source changes.
