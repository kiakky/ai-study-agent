# Milestone 3: generated practice and conversational teaching

Implemented in the isolated `practice-quiz-milestone3` worktree based on deployed
`86d8d84`. This document records implementation and synthetic verification, not a
live deployment or proof of live model quality.

## Delivered scope

- One private material library per authenticated user: pasted notes and text-based
  PDF uploads through the Mini App, plus PDFs through private Telegram messages.
  Private Telegram conversation can be opened to all /start-profile users through
  the explicit `onboarded_users` audience, without a tester list.
- Owner-only Brightspace evidence can feed generation through revision-pinned
  references returned by the existing read tools. Other users cannot select the
  owner's sources or read their derived quizzes, jobs, attempts or results.
- Native chat gains `study_material` and `prepare_practice` when explicitly enabled.
  The LLM chooses materials and quiz scope. Explanations use the same read evidence
  in normal chat; Python does not parse natural-language topic/course abbreviations.
- The native gateway's local tool-count bound increases from eight to ten to retain
  the existing five Calendar tools, three owner Brightspace reads and two practice
  tools. It remains bounded. No Calendar approval/execution tool is added.
- A private generation call writes one to ten MCQs (default five), in batches of
  at most five. Every question has four choices, an answer key, an explanation for
  every choice and verbatim supporting source quotations. Known source references,
  quoted-text membership, strict schemas, counts and duplicate questions are checked.
  These checks do not establish semantic correctness of every generated answer.
- The student-facing attempt API returns questions/options only until submission.
  Python marks against the saved LLM-authored key. Completed results include the
  selected-option explanation, correct-answer explanation and supporting quotations.
  No second grading inference is needed, and no adaptive difficulty is implemented.
- Every attempt has independent server-side answers, a revision counter and an
  immutable completed result. Stale tabs receive a conflict; retrying the identical
  completed submission returns its saved result. New quizzes do not erase old drafts.
- Device-local request snapshots preserve uncertain saves/submissions. A stale-save
  conflict requires explicitly reloading the server version; it is never overwritten
  silently. Separate start/generation request IDs protect network retries.
- Generated Practice has material preview/part selection, private generation status,
  available sets, unfinished attempts and recent results. The existing Dashboard,
  Quizzes and Grades pages link to it; Grades also lists generated results. Legacy
  demo attempts remain available and retain their separate labels.

## Storage and runtime

Four additive tables are defined in `services/practice_store.py`: `practice_materials`,
`practice_jobs`, `practice_sets`, `practice_attempts`. They reference existing users,
not fabricated exams. Existing exam-linked demo records are not repurposed.

Default database startup does **not** create these tables. Explicit provisioning uses
the reviewed schema or `initialize_database(backfill_approvals=False,
include_practice=True)` on a separately approved deployment. No production migration
has been performed. Existing live databases and credentials were not read by tests.

The feature is disabled unless ignored local `config/practice.json` contains exactly
`{"enabled": true}`. The tracked example remains disabled. With the switch enabled,
the single bot process starts one background practice worker. The API queues jobs;
the worker processes them outside web requests using the existing provider factory.
API and bot must use the same database/configuration. No new framework or service is
required, and no Calendar conflict heartbeat is introduced.

Jobs are claimed transactionally before inference. At most three pending and twenty
generation requests per user per rolling day are accepted. Inference is not retried
automatically after an uncertain response. On restart, running jobs fail as interrupted;
queued jobs remain available. No partial quiz is published, including interruption
between inference and publication. Students can explicitly make a new request.

Source excerpts are frozen at queue time. A job uses up to four selected excerpts,
each at most 6,000 characters, with an additional serialized context bound. Long
documents must be scoped to the relevant part; remote completeness is never assumed.

## PDF limits

PDFs are limited to 5 MB, checked for the PDF signature, parsed without network or
application secrets in a separate process with a fifteen-second timeout, and limited
to one hundred pages and 30,000 extracted characters. Excess/unsupported content is
rejected or clearly warned as incomplete. Encrypted files, scans without readable text,
diagrams, OCR, other file formats and external download URLs are unsupported.

Only extracted text and warnings are retained for uploads, not original binary files.
If extraction is truncated, later omitted sections must be supplied separately (for
example by pasting the relevant text). Uploaded filenames are display text, never
filesystem paths. The client renders material/model text with DOM text APIs.

## Scope deliberately unchanged

- No conflict heartbeat, autonomous Calendar monitoring or rescheduling implementation.
- No timed generation, proactive study reminders or scheduled content delivery.
- No new automatic assessment-discovery workflow or guaranteed whole-course coverage.
- No change to Brightspace startup/authentication, Google OAuth scopes, ngrok settings,
  production SQLite, live services, public publication checkout or GitHub.

## Verification and manual acceptance

Safe commands from this worktree:

```powershell
..\..\.venv\Scripts\python.exe -m unittest discover -s tests -p test_practice.py
..\..\.venv\Scripts\python.exe -m unittest discover -s tests
..\..\.venv\Scripts\python.exe tests/practice_browser_check.py
node --check frontend/practice.js
```

`practice_browser_check.py` follows the repository's existing headless Edge QA workflow.
Every browser request is intercepted and routed into FastAPI TestClient with synthetic
signed identity and temporary SQLite; external requests are blocked. No server or port
is started. Browser profiles and screenshots stay in ignored project test folders.
Windows can retain temporary Edge download handles after successful browser closure;
deferred cleanup is confined to these ignored synthetic folders.

Tests cover pasted/PDF input, actual bounded PDF extraction, malformed/oversized/scanned
files, ownership denials, private answer keys, immutable source snapshots, schema-only
opt-in, native chat/model protocol, queued generation, malformed/unsupported model output,
verbatim evidence, duplicate/concurrent jobs, interrupted inference, independent attempts,
stale saves, deterministic marking, submission replay and mobile end-to-end recovery.

After a separately approved deployment, live acceptance should ask for five questions
from a real read Brightspace source, test a second user's own pasted notes/PDF, leave two
quizzes unfinished, resume each, submit and inspect explanations/results. Cross-check
the semantic accuracy against the original materials. No live model/school operation
has been used in this implementation phase.

## Final verification

- Full regression: **387 tests passed**, including **34 new practice tests**; Python exit 0.
- Mobile Edge QA passed at 390 x 844: pasted material, mocked model generation,
  independent unfinished quizzes, resume, lost submission response, source-based review,
  shared Grades navigation, legacy demo draft preservation and XSS-as-data behavior.
- A generated-result screenshot was visually inspected; mobile overflow assertion passed.
- JavaScript syntax, changed Python syntax and staged whitespace checks passed.
- Brightspace startup, Google OAuth, Calendar executor and scheduler modules match
  deployed base `86d8d84`. The live checkout and its unrelated README/HANDOVER edits
  remain unchanged. No production database, private credentials, service process,
  school request, live inference or real Telegram message was used.

No deployment is authorized by these results. Before deployment, review the four-table
additive migration and private backup, explicitly enable the feature after provisioning,
and set conversation audience to `onboarded_users` as approved by the operator.
Live semantic quality of quiz questions/explanations and real-model tool selection still
require operator acceptance; mocked protocols cannot establish those properties.


## Public conversation audience (approved sandbox addition)

The operator approved conversational access for anyone who starts the bot in a private
chat. Set `audience` to `onboarded_users` in the existing conversation configuration
when deploying; preserve its provider/model and enabled setting. The example uses
this audience but remains disabled. Missing audience preserves the previous tester
mode; unknown audience values fail validation.

A user and onboarding row (created by /start) suffice. Completing preferences or
connecting Google is optional for chat. Unknown senders are directed to /start;
groups and mismatched sender/chat identities are ignored. Chat-history clearing,
preference confirmations and owned-session controls use the same audience. The
actual source, preferences, Calendar review and execution services still bind every
operation to the authenticated actor. Calendar consent and exact approval remain
necessary. Developer-only commands and the synthetic /calendartest helper remain
restricted. No owner Brightspace authorization, derived-content isolation, quiz
answer privacy or per-user data ownership changes.

The deployment must explicitly activate this audience; no live configuration or
service was changed during implementation. Tests use temporary SQLite, synthetic
Telegram senders and mocked inference/delivery only.

Public-access verification: seven additional tests cover new non-tester profiles,
private identity checks, unknown users, isolated history clearing, session controls,
the feature kill switch and configuration validation. Preference-confirmation
regression now exercises public users with an empty tester list. The full run
executed 394 tests: 393 passed and the synthetic Windows DPAPI test errored in the
restricted sandbox. All 11 OAuth tests, including that DPAPI case, then passed
outside the restricted sandbox. No credential files or live service were used.
Changed Python syntax and whitespace checks passed.


## Spontaneous quiz tool-loop repair (sandbox, not deployed)

Live acceptance reported a quiz request ending in `tool_loop` and a misleading
Calendar scheduling clarification. Sanitized live diagnostics confirm that error
code, but the earlier release did not log which native tool repeated. No live
conversation records or source material were inspected to guess that detail.

Practice instructions now explicitly distinguish immediate quizzes from Calendar
planning, course weeks from schedule dates, and reading evidence from preparing a
quiz. No session, exam date, Calendar connection or approval is required to queue
practice. Natural-language interpretation and source selection remain with the LLM.

The native loop permits one cached repeat of each identical material-list/read
request within the same state, returning guidance to use the data and continue.
It does not rerun the school call, refresh, generation job or Calendar operation;
repeated loops remain bounded by the existing turn, operation and time limits.
Brightspace owner/connection authorization is rechecked before cached reuse.
Calendar calls keep their existing repeat guard. A repeated successful identical
quiz preparation returns the saved receipt, avoiding another job and a false
failure after successful queuing. Final text for a confirmed queued job comes from
its status; it cannot require scheduling or claim generation is already complete.
Mixed Calendar reviews still use the exact approval interface.

Only known tool names and outcome codes are logged for future diagnosis. No tool
arguments, student identifiers, source text, keys or tokens are logged. The loop
error no longer requests session counts and dates for unrelated tasks.

Six synthetic regressions cover an owner course-read-to-generated-quiz path with
no Calendar connection/plan, one repeated course read, a bounded third repeat,
authorization revocation before reuse, duplicate preparation without another job,
and a queued-job receipt overriding an irrelevant scheduling clarification. Both
the repeated-read and duplicate-preparation scenarios reproduce `tool_loop` when
run against the deployed baseline implementation in a synthetic process.

No live service, configuration, database, credential, school data or Telegram
message was changed by this repair. Real-model source selection still needs live
acceptance after a separately approved deployment.

Verification: all **400 regression tests passed** (242.951 seconds, exit 0),
including six added spontaneous-quiz cases. Changed Python syntax and whitespace
checks passed. Synthetic baseline reproductions confirmed both tool-loop patterns.


## Mini App quiz delivery repair (sandbox, not deployed)

Phone acceptance showed a new quiz and its answer key delivered as Telegram prose.
The deployed native loop accepted any final prose reply when no saved job existed;
a synthetic run against baseline `d5577c6` reproduces that exact delivery gap.
Earlier synthetic tests had scripted the successful tool path and did not cover
this model deviation.

Practice instructions and the tool description now explicitly require ALL new
quizzes to use the saved Mini App path. Ordinary explanations stay conversational.
A final prose candidate without a saved quiz job or Calendar review receives one
separate bounded model check against the latest request and original bounded chat
context. No word/regex-based intent router is added. The check selects normal
conversation, a genuinely necessary source/topic clarification, or saved practice.
It has no execution tools, receives no backend actor/Calendar fields, and uses a
strict one-tool native response schema. It never delivers the candidate itself.

When practice is required, the candidate quiz/key is withheld and never added to
saved history or reused as source material. The existing agent continues with
required actor-bound tool calls until it saves a quiz job. The established receipt
and Telegram presentation then supply the Generated Practice Mini App button. If
the check is invalid or the agent again returns prose without saving, the request
fails closed with a sanitized message; no draft quiz/key is sent. At most one check
and recovery are allowed, within existing loop/operation/deadline bounds.

This adds one provider call to prose-only turns while practice is enabled,
including ordinary explanation replies; already successful saved-quiz and Calendar
review paths avoid that call. This is a latency tradeoff to keep semantic decisions
with the LLM while repairing a missing-tool response. Real gateway behavior and
semantic classification still require operator live testing. Mocked contracts do
not guarantee the model will classify every real request correctly.

Seven new synthetic tests cover withheld inline questions/key, repaired saved job
and actual Mini App keyboard construction, ordinary explanations, genuine source
clarification, ignored tool requirements, malformed decisions, school-quiz questions
that are not generation requests, and the actual provider wire schema with all
network requests mocked. Existing explanation regression is updated for the check.
No live service, configuration, database, school operation or Telegram message has
been changed during this repair. No deployment is authorized by test results.

Verification: **407 full regression tests passed** (253.432 seconds, exit 0).
The focused practice suite passed 47 tests; changed Python syntax and whitespace
checks passed. Baseline prose-only/no-job delivery was reproduced synthetically.
