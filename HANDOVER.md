# Developer handover — 15 October 2026

AI IGNITION 2026, PS2 **One Less Thing**. Deadline: **23 October 2026**.
Read README first for Windows setup. This is a source-only checkpoint based on deployed
`5c28756`, not a copy of the live machine. No private history, accounts or database are
transferred. Python remains the application and tool owner; Orchestrate is its reasoning
provider. Do not rebuild the backend inside IBM.

## Start here: evidence, defects and blockers

| Area | Actual state |
|---|---|
| Telegram / Mini App | Running laptop deployment; greetings, names and bounded persistent conversation have real user testing. Mini App demo quizzes/results/settings have prior mobile testing and mocked API/security tests. |
| Orchestrate | LIVE POC agent 1.0.0, Groq GPT-OSS 120B. Python IAM, LIVE execution and complete streaming reply extraction verified. Runtime remains optional legacy code, original Lite quota exhausted. |
| Calendar | Full Python read/propose/change/exact-approval execution paths implemented; Orchestrate structured-action bridge implemented. Prior Runtime Calendar tests do not establish current end-to-end acceptance. |
| **Known Calendar defect** | Latest repair `2414915` had **282 passing offline regressions**. Real Telegram clarification **“2 a week should be enough” produced an invalid tool request**. Unresolved. Conversational scheduling is NOT fully verified. Reproduce with synthetic context, inspect safe action metadata, add a regression, then fix narrowly. Never let rejected prose execute or blindly increase tool limits. |
| Google OAuth | Owner confirms External / **In production**. Unverified-app warning and applicable 100-user limit accepted. **Fresh unknown-user OAuth has not passed acceptance testing.** No change to Google verification status is claimed. |
| **Judge access** | Conversation and session controls still limited to **two testers**. Google Production does not remove this separate gate. Safe public onboarding is an unfinished prerequisite. |
| Brightspace | Read-only MCP/source extraction foundation, owner binding, revision/evidence structures and synthetic tests. No safe arbitrary-student session onboarding, real exam-discovery proof or deployed LMS reasoning. |
| Quizzes / learning memory | Demonstration quizzes, attempts/results/grades, profile/preferences and chat persistence exist. Grounded AI generation and durable weakness-driven adaptation remain future work. |
| Proactivity | Bot consent/reconciliation/menu maintenance exists. No general academic monitoring, adaptive planner or time-saved analytics yet. |
| Publication navigation | Homepage/privacy Telegram links and Settings → Connections guidance included here; not deployed to the original host by this handover. |

Do not market the open items as completed. The earlier 282 result is historical evidence,
not a new test run of this sanitized clone. See SECURITY_AUDIT for this checkpoint's checks.

## Original vision: complete intended student journey

1. Student opens Telegram and completes onboarding in their authenticated account.
2. Agent remembers study preferences, academic interests, scheduling requirements, strengths
   and weaknesses. Today profiles/history/results exist; semantic learning memory needs work.
3. Student optionally connects their own Google Calendar and their own Brightspace account.
   Google flow exists; safe per-student Brightspace login remains unimplemented.
4. Read-only Brightspace MCP retrieves course materials, announcements, exam information and
   deadlines. Evidence must preserve provenance, uncertainty and source ownership.
5. IBM Orchestrate reasons over authorized evidence, deadlines, preferences and availability.
6. Agent proposes personalized revision priorities and study schedules, respecting temporary
   instructions over saved defaults without silently changing permanent preferences.
7. Python reads real availability, validates timezone/conflicts and prepares exact proposals.
8. Student amends selected sessions conversationally, sees the whole revised proposal and
   explicitly approves consequential actions. Changed proposals invalidate old controls.
9. Python creates/reschedules/cancels only the approved owned events in that user's Calendar.
10. Mini App presents AI-generated quizzes grounded in authorized course material.
11. Scores, mistakes and weaknesses persist for personalized follow-up recommendations.
12. Opt-in monitoring detects meaningful new announcements, exams or deadline changes.
13. Agent explains changed evidence and adapts the plan; new Calendar changes need new approval.
14. Telegram sends relevant reminders/updates with consent and bounded delivery, not spam.
15. Record verified work completed and measured time saved against manual planning.

These steps are the target, not a claim all fifteen work today. A useful final demonstration
must complete tasks with several tools, persistent context, recovery and human approvals;
it must not be reduced to a generic chatbot or fabricated academic facts.

## Architecture and code ownership

Telegram authenticated updates → `bot.py` / `conversation_telegram.py` → conversations
service → provider factory → Orchestrate transport → validated action envelope → Python
Calendar agent tools → proposal store → explicit human decision → existing executors.
The model selects requests; it never authenticates users or directly calls Google.

- `backend/api.py`: public static pages, signed Telegram Mini App APIs, account-scoped quiz,
  preferences and integrations endpoints, Google callback. Private APIs must remain private.
- `backend/bot.py`: commands, callbacks, polling and maintenance tasks. Legacy owner-only
  `/schedule` is a separate path; it is not a public per-user identity shortcut.
- `backend/database.py`: SQLite initialization. Services own profiles, conversation history,
  attempts/grades, proposals/sessions, action/outbox/audit state and encrypted OAuth records.
  LMS stores source bindings, immutable revisions, extraction/index/evidence metadata.
- `services/conversations.py`: persistent bounded history, provider settings, saved defaults,
  request leases/serialization and orchestration. Free-text notes/account IDs are not sent
  as saved-default context. Normal user messages can themselves contain personal text.
- `llm_providers.py`, `orchestrate.py`, `orchestrate_client.py`: small replaceable provider,
  validated LIVE metadata, IAM cache, stream parser and redacted errors. Runtime retained.
- `orchestrate_actions.py`: internal structured response schema, strict operations/arguments,
  one bounded format repair. Unknown actions/identity claims fail closed.
- `calendar_agent.py`, `calendar_tool_inputs.py`: allowlisted actor-bound read/proposal calls.
  No LLM-accessible execute/approve operation.
- `calendar_conversation.py`, `pending_proposals.py`, `planning.py`, `scheduling_core.py`:
  local constraints, proposals, selected amendments, timezone/availability validation.
- `calendar_management.py`, `calendar_actions.py`, `calendar_text_decisions.py`,
  `calendar_change_decisions.py`, `calendar_telegram.py`: exact review, approval/rejection,
  creation/change executors, ownership, stale-control rejection and delivery.
- `google_oauth.py`, `token_cipher.py`: OAuth state, browser/PKCE/expiry/generation checks,
  actor-scoped credentials encrypted with Windows DPAPI and actor entropy.
- `brightspace.py`, `lms_mcp.py`, `lms_retrieval.py`, `source_store.py`,
  `document_extraction.py`: strictly read-only MCP boundary and source/evidence foundation.
- `frontend/`: framework-free Mini App and static pages. `scripts/run.py`: own-host launch
  and health helpers. `scripts/init_database.py`: refuses an existing database.

Inspect the actual function/schema before changing an interface. Do not duplicate Calendar
logic, introduce a hosted DB, Docker or a framework migration to finish this hackathon.

## Identity, privacy and approval invariants

Authenticated Telegram update sender or verified signed initData determines actor identity.
Never accept an LLM user ID, Google email, event ID ownership claim or arbitrary account
selector as authorization. Every read/proposal/decision/execution stays actor-scoped.
There is no student fallback to the developer's Calendar.

Saved preferences are soft defaults. Explicit task overrides are temporary; permanent
preference edits require the existing confirmation. Threads are fresh for each Orchestrate
invocation, with bounded local per-user history supplied explicitly. This prevents shared
remote conversational context, but is not an IBM retention/deletion guarantee.

LLM actions are internal JSON, never something students must type. Strict schema checks,
unknown-field rejection, bounded tool rounds and one format repair precede Python calls.
Prose claiming execution is not execution evidence. Python must confirm success before
reporting created/changed/deleted events. Provider uncertainty must not trigger blind writes.

Exact proposal digest/version, expiry, approval state, ownership, idempotency/outbox,
conflict rechecks and change ETags protect consequences. Modification is not approval;
“looks good, but move Friday” must revise, not execute. Revised/expired/rejected controls
must not work. Conversational explicit decisions use existing Python approval paths.

Scopes: Calendar free/busy plus owned-event management, primary Calendar. OAuth disconnect
removes the active local credential, not Google's grant/events/backups. Revoke in Google
separately. Token encryption is host/Windows-user bound, not portable full-DB encryption.
No plaintext fallback; no copying original client secrets/tokens into this repo. Privacy
requests use the approved public policy email; deletion is operator-managed, not automatic
self-service. Coordinate backup retention and provider-side limitations honestly.

## Public onboarding / judge accessibility blocker

Current gates are in `calendar_testing.configured_testers()` (private `calendar_test.json`),
`conversations.load_settings()` (audience only `calendar_testers`),
`conversation_telegram.handle_text`, clear-history and preference confirmation, plus
`calendar_telegram.session_list` and session callbacks. Legacy `/schedule` has OWNER_ID
checks. `/start` and signed Mini App account onboarding are already broader; do not assume
those prove chat/session access is public.

Implement one explicit, default-off public audience policy consistently across these
entry points. Keep Telegram verification, actor-scoped DB/OAuth/session queries, exact
approvals and owner-only administrative paths. Add global capacity/per-user pacing and
clear refusal/recovery, not merely deletion of membership checks. Review with the operator
before enabling. Never publish actual tester IDs.

Definition of done: two existing users retain private memory/connections; a third previously
unknown Telegram/Google user onboards without editing an email/tester allowlist; bad signed
identities, cross-user resource IDs, stale approvals and excessive requests are denied.
Fresh Google OAuth must be manually verified after Production publishing. Google warnings,
Workspace policy and applicable new-user cap remain potential judge blockers.

Current safeguards include 3,000-character input, 6,000-character local context, 12 retained
turns, per-user admission/serialization/leases, queued-request deadline 120 seconds and
bounded action budgets. Review actual config/code before tuning; do not raise limits to
hide scheduling loops. Public mode is NOT implemented by this checkpoint.

## Remaining work: recommended order and acceptance criteria

### A. Stabilize Calendar and safe judge onboarding first

Reproduce the exact two-week request and “2 a week should be enough” clarification using
mocked Calendar and synthetic context. Determine invalid schema/argument cause; add a
focused regression. Preserve whole-proposal review, selected amendments, explicit approval,
rejection/expiry/duplicates and no-write fail-closed behavior. Next-two-week resolution is
server-side; review its full-week semantics with the user rather than assuming rolling days.

Done when two real accounts pass: memory → own connection → availability/conflict → multiple
sessions → change one only → reject → new exact approval → correct actual events → move one
→ cancel one → temporary afternoon override leaves saved mornings unchanged. Test ambiguous
approval plus amendment, repeated/expired controls, provider failure and no duplicate events.
Real writes require owners' deliberate approvals, never automated debugging writes.

### B. Read-only Brightspace with each student's OWN account

Current MCP wrapper uses a positive read allowlist and requires `readOnlyHint: true`.
Unknown/write/submit/acknowledge/mark-read operations and arbitrary output paths are denied.
Subprocess uses an isolated session cwd and strips other integration credentials including
WXO. Pinned package: `brightspace-mcp-server@3.13.18`, updates disabled. Downloads default
off until trusted schema/transport/destination handling is verified.

Read `lms_mcp.py` tool negotiation and `lms_retrieval.py` specifications for course/resource
listing, initial discovery, exam-source discovery, document retrieval, search/evidence and
change checks. `SourceStore` still requires owner authorization AND explicit verified
`developer_session` binding. These structures are infrastructure, not product login or
proof that real exams have been interpreted correctly.

**Never let a student or teammate access the original developer's personal school account.**
A teammate may test their own school login on their own machine/OS profile and fresh private
session following README. Never copy/rebind the original Microsoft SSO/cookies/profile or
ask for school passwords in Telegram. Changing an environment variable on the shared host
or removing an owner check is NOT multi-user support.

Implement verified Telegram↔school-account↔session binding, per-user isolated transports,
connection lifecycle/expiry/re-auth, path/cache ownership and audit-safe failure handling
before exposing retrieval. Use institution-authorized browser SSO/MFA; institutional
restrictions may require an alternative supported route. Stop if unsupported.

Done: two independently authenticated students retrieve only their own courses/documents;
cross-user source IDs/session paths denied; expired auth prompts reconnect; every tool is
read-only; injected document instructions cannot cause actions. Demonstrate exam/date facts
with source citations and explicit unknowns. If real LMS access is unavailable, use labelled
synthetic course fixtures/uploaded authorized materials and disclose the fallback.

Extraction retains original bytes/hashes, immutable revisions and evidence segments. Text,
Markdown/HTML, text PDFs, DOCX and PPTX are supported foundations. No OCR/multimodal guarantee;
scans/charts/complex tables/reading order need explicit uncertainty, not invented facts.

### C. Grounded quizzes, grades and adaptive personalization

Depends on authorized evidence (or clearly labelled synthetic fixtures), not live LMS
availability alone. Keep existing Mini App attempts/grades/resume UI. Generate validated
questions/answers/explanations grounded in cited segments, reflecting exam relevance,
marks/difficulty and task overrides. Save source revision, answers, scores/mistakes and
weakness updates actor-scoped. Distinguish a demo quiz from generated course-grounded work.

Done: evidence-backed quiz → mobile attempt/resume/submit → correct persisted result after
restart → targeted follow-up on missed concepts. Cross-user attempt access/regrade denied;
invalid or unsupported source generation fails clearly. Include answer-key/grounding review,
not only valid JSON. Preference changes still require confirmation.

### D. Opt-in monitoring, adaptation and notifications

Depends on B's ownership/evidence and A's safe proposals. Add bounded durable jobs, consent,
revision comparisons, deduplicated notifications and retries. Detect material exam/deadline
changes; ignore cosmetic changes. Explain cited before/after evidence, propose a revised
plan and require new exact approval before touching Calendar. No monitoring writes to LMS.

Done: synthetic changed deadline detected once → notification/revised proposal → rejection
leaves Calendar unchanged → explicit approval modifies only selected owned sessions.
Restart/retry does not duplicate jobs/events/messages. Failed provider/SSO recovers safely;
quiet hours/rate bounds work. Existing bot maintenance is not this new monitoring system.

### E. Final integration, evaluation and evidence

Combine A–D with two real accounts and one fresh judge account. Label simulated sources.
Record actual manual baseline/time and verified agent actions (discovery, availability,
proposal, approval, quiz, changed evidence/replan). Do not invent time-saved metrics.
Confirm official submission deliverables/rules with organizers: repository access, demo
video, slide/problem statement, required IBM attribution and October 23 cutoff. Do not
claim unchecked requirements are satisfied. Rehearse laptop/network loss recovery and
prepare screenshots/video plus labelled fixtures if institutional APIs are unavailable.

Suggested four teammate ownership lanes: (1) Calendar/public onboarding/security,
(2) read-only LMS/account binding, (3) grounded quizzes/learning memory, (4) monitoring,
demo/testing/docs. Lane 4 can build synthetic change fixtures before real LMS is ready;
all merge behind the same Python authorization/approval boundary. Agree interface contracts
before parallel edits. Operator alone coordinates live deployment.

## Host maintenance and recovery

The existing Windows laptop remains host. One FastAPI process on loopback, one Telegram
poller, existing stable ngrok. Do not run this clone with the live bot token or duplicate
pollers. No separate LMS proactive worker exists yet. Laptop sleep/network loss interrupts
availability. DPAPI tokens require the same OS user; moving DB alone cannot restore them.

For your own clone use README setup/start commands and own accounts. On the original host,
coordinate explicitly approved deployment: inspect source/processes → targeted tests →
non-consuming Telegram queue/webhook metadata check (never advance offset or drop pending
updates) → consistent fresh private SQLite backup via supported backup API, read-only source,
verify integrity without reading records → stop only known API/bot → deploy reviewed commit
→ start exactly one each → preserve ngrok → local/public health, protected-route checks,
polling status → phone acceptance. Do not inspect/export student records for convenience.
Schema changes need separate reviewed additive migration approval; never reset/restore DB
as a generic fix. Keep private backups Git-ignored and do not copy credential files.

Troubleshooting: inspect sanitized error classes/operation counts, not tokens/prompts/history.
401 private API outside Telegram is expected. Telegram conflict often means two pollers.
Invalid action is fail-closed, not permission to execute prose. Verify selected provider,
LIVE metadata and own credentials via existing app helpers, not credential dumps. OAuth
redirect must match stable origin; reconnect only the affected actor if necessary. Treat
uncertain external outcomes separately from Telegram send failures; do not replay writes.

## Handover boundaries and next verification

No Calendar bug fix, public enablement, live OAuth test, school retrieval, generated quiz,
proactive feature or production restart is included here. Static navigation is undeployed.
Keep repo private, use reviewed branches/PRs, audit contents AND history before publishing.
Never graft private runtime history onto this independent sanitized repository.
SECURITY_AUDIT records focused tests/audit evidence; final publication receipt supplies
remote branch/commit. New teammates should clone that remote normally and use their own
accounts. The original working application and credentials stay private and untouched.
