# Orchestrate Calendar reasoning adapter — deployment pending

The isolated `orchestrate-calendar` branch builds on deployed checkpoint `3c3f289`.
Production source remains unchanged. The separate sanitized publication snapshot under
`.local-backups/publication/ai-study-agent` is not the running source and is untouched.

## Integration approach

Both providers use the existing actor-bound `CalendarAgent`. Runtime keeps its native
function-call path. Orchestrate reuses the verified IAM/LIVE client and streaming
parser, but the supported Run API accepts a message rather than Runtime tool definitions.
The adapter therefore prompts for a strict JSON action envelope and validates it locally;
it does not claim IBM-native function calls or import Python tools into IBM.

The envelope contains only `content` and `tool_calls`; each action contains a fixed
allowlisted name and schema-validated arguments. Python generates call IDs. Unknown
operations, identity arguments, duplicate keys/calls and invalid arguments fail closed.
A malformed envelope or missing required choice permits one format-only repair before
any Python operation. Network/authentication/quota/uncertain failures are never replayed.
Actual argument schemas, including rebased definitions, are included in the response
schema in the prompt; this is not a provider-side response_format guarantee.

The inert router distinguishes chat, new planning, pending amendments, preferences and
Calendar management. A validated Calendar task subtype narrows existing-event changes
to actor-owned session lookup and proposal tools. Pending draft state comes from Python.
First Calendar actions are mandatory; final Calendar read replies are rendered from
confirmed Python results so model prose cannot assert an unexecuted write succeeded.
Normal conversation remains natural language. Students never supply structured actions.

## Reused workflow and safeguards

- Read normalized saved defaults/timezone and own connection status; find actual free slots.
- Single/batch plans use returned slot references, not invented draft dates.
- Current instructions override saved defaults locally, without saving settings.
- Selected pending amendments preserve other sessions, revalidate the full schedule and
  invalidate prior reviews. The complete revised schedule is presented again.
- Existing sessions are retrieved by actor-bound filters/ordinal; moves/cancellations
  become separate frozen reviews, with ownership, remote event and ETag verification.
- A newer pending change supersedes old pending controls for that same session. An
  approved/processing/uncertain change blocks competing proposals.
- Clear whole-message human approvals/rejections bypass the model and use the existing
  creation/change executors. Mixed changes plus approval never authorize a write.
- Text decisions bind durably to the latest presented exact review; count/kind/reply
  mismatches, expired/stale/early/cross-user decisions fail closed. Duplicate delivery
  cannot attach to a newer review. An older plan cannot be text-approved after a newer
  session-change review. Existing button approval/consent/outbox paths remain available.
- Python confirms successful writes, preserves idempotency, rechecks conflicts and
  connection generation, and reports uncertain outcomes without inventing success.
- Disconnected users receive a Settings Mini App button, connect their own Google account,
  then say “Continue with that study plan”. Local history retains the request. There is
  no developer-account fallback and no automatic unapproved event creation after OAuth.
- Permanent preference changes still require the existing exact preference confirmation.
  Free-text restrictions fail closed until mapped through the existing settings workflow.

## Isolation and provider configuration

The existing factory selects `watsonx` or `orchestrate`; no automatic fallback or new
framework/database is added. The two-tester gate remains in place. Every action is bound
to authenticated Telegram identity in Python; model IDs and account claims never authorize
anything. Fresh remote threads are used on each inference, with bounded per-user SQLite
history and paired tool results replayed as data. Local SQLite remains authoritative for
history, proposal ownership, exact approvals, deduplication, receipts and restart recovery.

The shared client keeps TLS, in-memory IAM caching, LIVE version/model checks and zero
inference network retries. Remote tools, collaborators, hooks, memory and knowledge bases
remain forbidden: this change exposes only locally validated read/proposal actions.
The deployed IBM agent is neither changed nor redeployed.

Outbound defaults are the user-approved normalized fields. Tool payloads exclude saved
notes, credentials, account/Telegram IDs, Google event IDs and OAuth URLs. Opaque local
proposal/action references select only records owned by the authenticated caller.
Brightspace continues its existing read-only allowlist and WXO_* subprocess filtering;
no LMS or quiz tools are exposed, and no private LMS content is accessed.

## Verification and limitations

Focused tests cover strict JSON/streaming, bounded repair, unknown/identity arguments,
two-user concurrency and persistence, temporary defaults, missing OAuth/resume, conflicts,
selected amendments, exact approval/rejection, stale/duplicate/expired decisions, moves,
cancellation, failed review delivery, provider failures and bounded tool loops. The full
existing suite also covers Telegram commands, Mini App security, OAuth/executors, SQLite
and read-only LMS behavior using synthetic data and mocked external integrations.

Live evidence uses real Orchestrate reasoning with temporary SQLite and mocked Google:
previously verified three-session planning; this continuation verified selected Friday
amendment (2 submissions), existing-session move (3) and cancellation (3). Amendment
preserved other sessions and invalidated the old review. Move/cancel selected only the
second actor-owned session. Earlier move probes exposed routing failures; those failed
safely without execution, and the validated subtype/required lookup resolved them.
The interrupted earlier run has no recoverable result and is not counted as evidence.
No real Calendar events, Telegram messages or Brightspace operations were performed.

Real Telegram/OAuth/Calendar acceptance for this adapter remains pending. Model adherence
is not perfect; invalid output fails safely and may require a fresh student message.
Ambiguous requests ask for clarification; strict human approval grammar intentionally
requires explicit consent (“Approve all three”, “Approve this move/cancellation”).
“Looks good” alone is not approval. Default tools handle one existing-event change per
review; several existing-event changes require individual reviews. The run API has no
verified max_tokens equivalent; input/output, action loops and repairs are bounded locally.
Conversational facts persist within existing bounded history, not indefinite new memory.

## Commands and deployment gate

From the isolated worktree, use the existing root virtual environment:

```powershell
..\..\.venv\Scripts\python.exe -m unittest discover -s tests
..\..\.venv\Scripts\python.exe scripts/check_orchestrate_calendar.py
# Opt-in synthetic live verification; already performed, do not repeat routinely:
..\..\.venv\Scripts\python.exe scripts/check_orchestrate_calendar.py --verify --env-file ..\..\.env --scenario amend
```

No migration or production configuration change is required. Deployment needs explicit
approval and the established verified private SQLite backup, non-consuming queue check,
controlled single API/bot restart, unchanged ngrok and two-tester restriction.
After rollout both testers should independently propose, amend, reject/approve, retrieve,
move/cancel their own sessions, test temporary overrides and verify cross-account isolation.
Actual Calendar writes during acceptance occur only after each student's exact approval.

Official API references:
- https://developer.watson-orchestrate.ibm.com/apis/server_openapi.json
- https://developer.watson-orchestrate.ibm.com/environment/initiate_environment
- https://developer.watson-orchestrate.ibm.com/conversation/context_variables
