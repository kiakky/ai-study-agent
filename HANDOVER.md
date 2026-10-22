# AI Study Agent - teammate handover

Updated **20 October 2026**. Deadline **23 October 2026**. Read this before editing.
The developer stopped further speculative owner-quiz debugging to conserve remaining
weekly coding-agent usage. The next owner is a teammate; do not restart the broad
investigation or silently promise a complete workflow.

## Immediate state and next assignments

Application source deployed on the original Windows laptop: **`f76c2d6`**.
This handover is a documentation/source publication; it does not redeploy anything.
All preserved worktrees and the live database remain in place. Pre-existing README
and HANDOVER versions were backed up locally under ignored `.local-backups/` before
this rewrite. They contained obsolete architecture/scope statements; this document
supersedes those statements. Historical docs remain useful only with their dates/base.

1. **Assign one teammate to the background generation blocker below.** First add
   narrow, safe stage/error-category diagnostics to `practice.run_one`; capture one
   supervised owner reproduction. Diagnose the actual failure before changing policy.
2. **Assign demo acceptance/recording.** Prove the known PDF flow first. Separately
   test owner retrieval, Calendar plan/approval, scheduled delivery and adaptation.
3. Clean duplicate Telegram messages and obvious Mini App mobile layout issues only
   after the blocker is resolved or explicitly accepted as a demo limitation.
4. Complete README status, slides/video, measured time-saving evidence and submission.
   Assign the live-host operator and backup; GitHub does not keep the bot running.

These are assignments to agree as a team, not claims that anyone has accepted them.
Do not describe the remaining work as a measured "10%": key demo behaviors remain unverified.

## End goal: the complete student journey

This section is the product requirement, not a list of completed features.

| Stage | Expected student-visible behavior | Evidence / boundary needed |
|---|---|---|
| Access | Any student can `/start`, onboard, converse, set preferences and open the Mini App | Signed Telegram identity and user-owned records; no owner privileges granted by onboarding |
| Discover | Owner asks for exam help; LLM inspects course catalog, teaching plans, assessment information and notes | Actual read-only Brightspace evidence; other students use their own uploads |
| Prioritize | Identify real tests/exams and prioritize nearer ones | Cite actual scope/date evidence; never classify an assignment due date as an exam |
| Plan | Propose study sessions leading up to assessments, respecting preferences and Calendar availability | Generic plan if no exam date is evidenced; no invented dates |
| Revise/approve | Student changes a proposed session and reviews exact Calendar changes | Frozen proposal, actor ownership and explicit approval before writes |
| Study support | At session time, student receives a proactive notification and can ask for explanations | Actual relevant learning notes, not merely teaching-plan topic lists |
| Scheduled quiz | Session provides a generated Mini App quiz linked to the study content | Session trigger, available owned learning evidence, completed generation and private set |
| Spontaneous quiz | "Generate a short practice quiz for GRSP W3 now" works independently | No plan, Calendar, date range or session count required; LLM chooses evidence/tools |
| Attempt/review | Untimed quiz saves progress independently, resumes, submits and shows score/explanations | No answer key before submission; server-side independent attempts and actor isolation |
| Adapt | Periodic check notices a new Calendar conflict and suggests available alternatives | Deduplicated notification, current availability and fresh approval before moving events |

For the adaptation demo the developer can manually introduce an overlapping event.
Normal monitoring may run every 5-10 minutes; demo polling every 30-120 seconds.
Announcement monitoring was dropped. Do not substitute announcement monitoring for
Calendar-conflict adaptation. Do not require one complex prompt to perform every
stage: standalone requests are enough for the hackathon.

### Remaining work in practical order

1. Resolve or explicitly accept the **owner background generation blocker**. Preserve
   the working nonowner PDF path. The minimum quiz quality target is demonstrable
   grounding in real learning content, not production-level pedagogy.
2. Review quiz quality only as necessary: valid questions/answers, supported explanations,
   learning rather than administration questions, assessment scope when requested.
   Do not invent content or bypass grounding to obtain a green status.
3. Clean Telegram responses: the latest screenshot shows both a saved-request message/
   button and a second preparing response. Improve clarity without rerunning inference,
   duplicating jobs or claiming a queued job is ready. Final failed-job feedback needs care.
4. Refine Mini App mobile layout, spacing, loading/error states and navigation using
   existing HTML/CSS/JS. Keep attempt persistence and answer-key privacy intact.
5. Run owner and nonowner acceptance. Separately verify the Calendar planning slice.
6. Assess and implement the missing scheduled study support and Calendar heartbeat
   hooks if required for the chosen demo; these are not demonstrated by existing cleanup.
   Reuse current session management and approval executors; avoid a parallel scheduler
   architecture or experimental coordinator. Record any incomplete behavior honestly.
7. Record the successful demo, prepare slides/submission, measure time savings and
   assign an available host operator. Confirm organizer-specific deliverables separately.

## Current owner blocker

Latest user test, approximately **20 October 00:00 Asia/Singapore**:

```text
Generate a short practice quiz for my GRSP module's real quiz in w3 now.
```

Telegram reported a saved practice request and linked Generated Practice. The Mini
App showed "Preparation failed or was interrupted. No partial quiz was published".
A separately approved read-only metadata inspection found exactly one job in the
narrow test window: **status failed, error_code generation_failed, no published set**.
No source/message text, credentials or identifiers were displayed/exported; records
were not modified. The scoped log check found no detailed failure markers.

**Confirmed boundary:** the request got past discovery/queueing into the background
job path. This does not establish correct source selection, quiz quality or successful
publication. The worker's broad exception handler maps almost everything to
`generation_failed` and discards the original exception. Therefore the actual failed
stage and exception are **unknown**. Do not call this a proven provider error, content
review rejection, authorization error, token limit or blank-padding recurrence.

### Exact code path and smallest next investigation

- `practice_native.operation` -> `practice.request_practice` -> source qualification ->
  `PracticeStore.enqueue`. A saved job is preparing, not a completed quiz.
- `bot.post_init` starts `practice.worker` when practice is enabled.
- `practice.run_one` claims a queued job in a transaction, constructs the provider,
  rechecks LMS owner authorization, qualifies if needed, calls `generate`, rechecks
  authorization, then publishes the complete set transactionally.
- `generate` writes batches of at most five questions using `deliver_practice`, validates
  schema/count/citations, and calls `practice_grounding.review`. One correction is allowed
  for returned invalid content. Unknown/uncertain transport failures are not replayed.
- The broad catch stores `generation_uncertain` only for `inference_uncertain`;
  most other exceptions become `generation_failed`. Restarted running jobs become
  `generation_interrupted`. The latest job was **generation_failed**, not interrupted.

Instrument the worker with **constant stages and allowlisted categories only**:
provider creation, authorization, qualification, generation/validation/review and
publication. Existing `provider.last_error_metadata` can supply bounded HTTP status/
known provider codes. Never log exception text, request payloads, answers, source
passages, account IDs, URLs containing credentials or raw provider responses.
`preparation_stage` currently covers synchronous preparation, not this worker.
Reproduce the resulting category with synthetic sources and a temporary database;
make the smallest fix, run the relevant regression, then request deployment approval.
Do not replay/change the failed production job or inspect its frozen evidence without
new explicit approval. Prior inspection permission covered only metadata for named turns/jobs.

The final attempt ended because the necessary historical exception was not retained.
**No speculative application fix was made and no additional release was deployed.**

### Bounded next patch and its verification

This is a recommended next action for the teammate, **not an implemented patch**:

1. Add a local constant stage variable or a small existing-style context wrapper to
   `run_one`, covering provider setup, owner authorization, optional qualification,
   generation and publication. To distinguish draft from review, add equally small
   stage markers around the relevant `generate` calls. Avoid a new telemetry system.
2. Preserve the existing job states/error codes, transactional claim/publication,
   provider cleanup and no-replay behavior. Only add sanitized diagnostics. Use a
   fixed mapping from known exception/provider codes to categories; never `str(error)`,
   `repr(error)`, traceback payloads or arbitrary metadata dictionaries.
3. Add focused synthetic tests for one failing provider, source authorization denial,
   returned invalid generation/review and publication failure. Verify no set is
   published, no second job/inference replay occurs, and private marker strings do
   not appear in logs. Reuse existing fixtures rather than duplicating the application.
4. Report the concrete diff and checks, obtain deployment approval, then request
   permission for the precise diagnostic metadata needed from one owner reproduction.
   Existing historical permissions do not extend to new live records.
5. Use that category to choose **one** causal correction. If diagnostics still do
   not distinguish the failure, say what additional evidence is needed before making
   another behavioral change. Do not lower review/grounding requirements speculatively.

The source cannot reconstruct an exception that was never retained. A future
synthetic success is supporting regression evidence, not proof about that historical
job. The user explicitly chose to stop the original agent's debugging and hand off;
the teammate should agree a new bounded work allocation before continuing.

## Progress already made - do not repeat investigation

| Commit | Change and practical limit |
|---|---|
| `4aa351a` | Source qualification, learning-grounded generation/review and scope restrictions; synthetic evidence is not live acceptance |
| `00592b7` | Known inline-size refusal permits private temporary download up to 20 MiB, then extraction; unrelated read failures are not size failures |
| `c0c8235` | Compact catalogs with parent titles, per-turn snapshots, evidence feedback and completion recovery; time/repetition bounds replace fixed total call/round count |
| `f76c2d6` | Shared readable text view removes PDF layout padding consistently for read/selection; specific insufficient-excerpt recovery and safe preparation diagnostics |

Earlier live reads contained 6,000 characters with only 108 non-whitespace characters,
including 2,670 leading blanks. A synthetic reproduction established that selecting
that raw prefix can fail despite text elsewhere. The fix preserves original segments
and revisions, uses readable-view offsets in both read/preparation, and leaves uploaded
note coordinates unchanged. The earlier historical selection arguments/exception were
not persisted; it was never proved to be the sole cause of those live failures.

The alleged provider ten-tool ceiling was a **local advertised-function allowance**.
Eleven offered tools were verified with real IBM synthetic calls; the wrapper now permits
sixteen (only eleven verified). There is **no fixed total tool-call limit** in the main
loop; a 90-second deadline and repeated-no-new-information detection bound execution.
Do not confuse advertised function count with the number of tool executions.

`BrightspaceRetrieval.search_learning_materials` exists but is **not** exposed by the
native facade (courses/materials/read). Its local search is lexical. Adding search is
not the demonstrated fix for the latest worker failure. Do not revive the excluded
multi-goal coordinator, hardcoded course/week/intent routing, broad cap increases or
authentication redesign. Pinned upstream MCP 3.13.18 auth/refresh/login side effects
were explicitly accepted for this PoC; the controlled-startup experiments were discarded.

## Implemented versus verified

- **Live-tested:** nonowner uploaded-PDF -> generated quiz -> Mini App flow, per developer
  report. Latest owner request saved a job but failed before publishing a quiz.
- **Implemented and synthetic-tested:** actor-owned material/quiz/attempt storage,
  private answer keys until submission, independent resume/save, scores/explanations,
  cross-user denials; owner source gate; Calendar tools and exact approval boundaries.
- **Not established end to end:** reliable owner Brightspace generation, real assessment
  discovery -> revision plan, scheduled session quiz delivery, proactive study-time
  notifications, periodic Calendar conflict monitoring and approved adaptation.
- **Source check:** `bot.maintenance_loop` reconciles previous approvals at startup and
  expires requests every 30 seconds. It is **not** a Calendar conflict heartbeat.
  No session-triggered practice-generation hook was established in this review.
- **Needs user acceptance:** complete current owner/nonowner flows, fresh-account OAuth,
  plan amendment/approval, Mini App results and isolation. Do not revive historical bugs
  as proven current failures without reproducing them.

## Scope to preserve

Anyone completing `/start` should receive ordinary features. Brightspace is strictly
for the configured environment owner; teammates configure their own school account.
No public per-student Brightspace onboarding is planned. Nonowners use pasted notes/
PDFs. All material, citations, quizzes, attempts and progress must remain actor-owned.

Both spontaneous and scheduled practice are required in the intended demo, but their
implementation/verification differs as noted above. Spontaneous practice must not
require a plan, Calendar connection, dates or session count. Use actual learning
explanations; teaching plans establish scope only. Never infer exam dates from assignment
deadlines or fabricate dates. Without dates, offer generic planning. No answer keys
in Telegram; quizzes are untimed, with independent saved progress and wrong-answer review.

**Excluded:** adaptive difficulty, announcement monitoring, complex combined requests
and the experimental coordinator. Standalone requests suffice. IBM provider/model stays.

Completed practice attempts now also provide an actor-scoped topic-accuracy summary, a
review recommendation in Generated Practice, and a bounded summary for that same student's
next bot conversation. This is a review-focus signal only: adaptive question difficulty is
still excluded, and the summaries describe only completed practice attempts.

Intended adaptation demonstration: manually introduce a Calendar event overlapping an
approved study session; a periodic check finds it, notifies the student, proposes available
alternatives and waits for exact approval before changing Calendar. Normal interval can
be 5-10 minutes, demo 30-120 seconds. This is a requirement, **not a verified feature**.

## Tests already run

Do not rerun the entire historical investigation to regain confidence.

- Earlier independent full suite: 486 tests; 472 passed, 12 skipped, two issues.
  The obsolete forced-tool-choice assertion was corrected and focused checks passed;
  the Windows DPAPI synthetic check passed separately outside sandbox restrictions.
  Eleven skips were historical diagnostics and one platform-specific.
- Layout candidate: learning-text/LMS/quiz-refinement focused run **46 passed**;
  after diagnostic logging, learning-text/practice **53 passed** (overlap, not additive);
  native completion **8 passed**.
- Deployment `f76c2d6`: **14 focused regression tests passed** in root; syntax and scoped
  whitespace checks passed. No full-suite rerun was performed.
- Layout-heavy synthetic PDF plus actual IBM: first trial HTTP 500 before tools; a
  separate trial completed a saved three-question quiz in **16.52 seconds**, with
  harness grounding/resume/submission/scoring/isolation assertions. No real school,
  Calendar or Telegram operations were used. This did not prove the live owner case.
- Older combined synthetic cases covered owner, assessment, long documents, repeated
  filenames, missing material and nonowner PDF. Earlier experimental variants had mixed
  success; do not describe all variants as one reliable implementation.

Focused command and setup are in README. Full offline suite, only when changes warrant:
`python -m unittest discover -s tests -p "test_*.py"`. Use the project venv and temporary
DB fixtures. Historical logs, traces and worktrees are local/ignored, not teammate
runtime dependencies. Never upload real school records or credentials to reproduce a test.

## Useful preserved investigations

These paths exist **only on the original host** under `.worktrees/`; they are not pushed
as source. Do not replace current deployed files with their uncommitted variants.

| Worktree | Meaning |
|---|---|
| `owner-tool-flow` | Earlier experimental paging/recovery; uncommitted, not approved candidate |
| `owner-quiz-independent` | Independent tool-cap/discovery/loop experiments and synthetic traces |
| `owner-preparation-failure` | Layout-padding reproduction, metadata-only diagnostic and tested fix |
| `owner-final-attempt` | Clean `f76c2d6` checkout created for the last attempt; no speculative fix |

Current tracked notes: `docs/owner-quiz-loop-release.md`,
`docs/owner-preparation-layout-investigation.md`, `docs/quiz-refinement-batch1.md`,
`docs/brightspace-private-reading-release.md`, `docs/brightspace-upstream-startup.md`.
Read their limits; earlier statements such as fixed tool limits or disabled retrieval
may be superseded by later source. Local deployment records are ignored JSON artifacts.

## Independent services and hosting

**Existing public Telegram bot: abbi,
[@AIStudyAgentTPBot](https://t.me/AIStudyAgentTPBot).** The developer wants the team
to be able to continue using this identity. A new bot is not required for a shared
host takeover. Telegram retains the identity, but the Python polling process and
its practice worker must run on the designated host to provide service. The API,
Mini App assets and public HTTPS connection must also be available on that host.
The original laptop can be off only after the replacement backend is running.

Follow README setup using your own bot, database and credentials. The tracked examples
are deliberately disabled. Conversation audience supports `onboarded_users`; it does
not grant Brightspace owner rights. Use `provider: orchestrate` for this hackathon.
The active native gateway uses `groq/openai/gpt-oss-120b` through IBM Orchestrate, not
remote Calendar tool registration or an independently selected replacement provider.

Google OAuth: create your own approved web client, use ignored
`backend/credentials/google_multiuser_client.json`, configure your HTTPS origin and
callback `/oauth/google/callback` (confirm `google_oauth.OAuthSettings.redirect_uri`),
and set local Google feature flags deliberately. Windows DPAPI-encrypted grants do
not transfer to another Windows user/machine. Google console publishing is not hosting.

Brightspace: configure ignored `config/brightspace_runtime.json` or the corresponding
runtime environment with `LMS_MCP_PACKAGE_ROOT`, `LMS_SESSION_DIR`, `LMS_TENANT_ORIGIN`,
`LMS_EXPECTED_ACCOUNT`, `LMS_NODE_EXECUTABLE`. Paths must be absolute; tenant is an
HTTPS origin. Install pinned MCP 3.13.18 and legitimately authenticate your own school
account through upstream browser SSO/MFA. A separate owner-approved SourceStore
`developer_session` binding is required; never silently bind/reuse another person's
session. Do not run historical operator/status scripts as a generic setup step:
some load protected session material. Read `lms_upstream_startup.py` and relevant docs.

Original live host: Windows laptop at `C:\Users\USER\Downloads\AI-Study-Agent`, one
Telegram poller, FastAPI on loopback port 8000, ngrok public HTTPS. Closing the host
or tunnel takes the demo offline. Hosting transfer requires an agreed operator,
private supported secret provisioning, approved database handling, matching OAuth
redirect/origin and a deliberate single-poller cutover. Do not copy DPAPI blobs or
school sessions and assume they work. `/start` does not prove every external integration.

### Decide which environment you are setting up

**Independent development/demo:** use a new bot token, your own positive owner ID,
new SQLite database, your authorized IBM credentials, your own Google OAuth client,
your own public origin/tunnel and your own Brightspace login/session. The original
laptop can be off; its bot identity, data and grants will not appear in your clone.
This is the recommended way for multiple teammates to develop independently.

**Replacing the host of the existing public bot:** coordinate with the bot owner.
This is an operational cutover, not an independent clone. Secret provisioning,
existing user data, Google grants, public URL and school session authorization need
separate decisions. Never start a second poller to "see whether it works". Existing
Windows DPAPI grants are not portable; users may need to reconnect on the new host.
No database export/migration or credential transfer is authorized by this document.

For the team's shared abbi demo, designate one teammate/host as the active operator.
Keep the bot token only by authorized private provisioning; it is not included in
GitHub. Configure IBM, Google, Brightspace, the public origin and local paths for
that agreed environment. Keep API and bot on the same token/database/configuration.
This is not a plan to let every teammate independently poll the same public bot.
Separate experimental bots are optional and useful for concurrent independent work.

#### Component-by-component takeover checklist for abbi

| Part | Reuse or configure | Verify before turning off the original host |
|---|---|---|
| Public bot | Reuse `@AIStudyAgentTPBot`; privately provision its existing token with owner authorization | Original poller stopped, exactly one replacement poller; `/start` and replies work |
| Mini App frontend/API | Deploy repository assets and FastAPI on replacement host; update `mini_app_url` | Dashboard menu/button target, Telegram-signed API access, practice attempts and grades |
| ngrok/public HTTPS | Configure replacement host's authorized tunnel/assigned stable origin | Public health/static pages; origin and Google callback match; no assumption of old hostname ownership |
| IBM Orchestrate | Configure authorized `WXO_*` credentials for the agreed instance/agent | Basic inference then the actual tool/generation path; do not stop at a greeting |
| Google Calendar | Configure authorized OAuth client, local flags and matching new redirect | Each student reconnects as required; own account only; no event changes without exact approval |
| Brightspace | Configure Node/package/session paths, tenant/account and legitimate owner login | Owner-only connection/read works; nonowner denied; no copied school session |
| Telegram owner binding | Deliberately retain or change the environment owner as agreed | Owner ID, school account and DB connection agree; no silent existing-binding overwrite |
| SQLite/state | Explicit fresh environment or separately approved supported migration | Correct practice/LMS schema, persistence, isolation; no assumption that DPAPI grants are portable |
| Runtime/uptime | Supervise API, tunnel and one bot/worker on the replacement host | Restart behavior, private logs, persistent storage and operator access for SSO renewal |

User-facing bot identity can stay unchanged while its backend host changes. A fresh
database nevertheless means fresh backend profiles/history unless a separate data
migration is approved. Inform testers if old quiz results or connections will not
be available. Never copy the original Windows profile or decrypt/export grants just
to preserve the appearance of continuity.

### Complete configuration examples (placeholders only)

These examples contain no working credentials. Replace placeholders privately,
never in Git or an AI chat. Use valid JSON with double quotes, booleans and no comments.
The existing strict loaders reject extra fields. Bot and API must run from the same
project checkout and use consistent configuration and the same bot token.

Private `.env` values needed by the current application:

```dotenv
TELEGRAM_BOT_TOKEN=YOUR_OWN_BOTFATHER_TOKEN
TELEGRAM_OWNER_ID=YOUR_OWN_POSITIVE_NUMERIC_TELEGRAM_ID
WXO_API_URL=YOUR_AUTHORIZED_IBM_ORCHESTRATE_INSTANCE_URL
WXO_API_KEY=YOUR_AUTHORIZED_IBM_ORCHESTRATE_KEY
WXO_AGENT_ID=YOUR_AUTHORIZED_DEPLOYED_LIVE_AGENT_UUID
```

Do not use these literal placeholders to launch. An operator can obtain their numeric
Telegram identity through a trusted account mechanism; do not make the LLM invent it.
Changing the owner ID does not migrate an existing LMS connection to the new actor.
Use a new DB for a genuinely independent environment.

`config/conversation.json` for ordinary onboarded users:

```json
{
  "enabled": true,
  "audience": "onboarded_users",
  "provider": "orchestrate",
  "model_id": "groq/openai/gpt-oss-120b"
}
```

`model_id` is present in the generic conversation settings; the native Orchestrate
provider's `MODEL` constant determines the current gateway model. Changing this JSON
string alone is not a supported model migration. Keep the hackathon IBM path.
If using `calendar_testers` instead, `config/calendar_test.json` must explicitly
enable the intended tester IDs. This is an optional restricted test mode, not the
product's intended public-onboarded audience. Test commands are separate from ordinary chat.

`config/practice.json`:

```json
{"enabled": true}
```

`config/deployment.json`, using an example origin that the operator must replace:

```json
{
  "mini_app_url": "https://your-assigned-host.example.com",
  "ngrok_executable": "C:/Tools/ngrok/ngrok.exe"
}
```

Use your actual assigned public HTTPS DNS origin, with no path, query, credentials,
fragment or nonstandard port. An ngrok executable on PATH can simply be `ngrok`.
The `run.py tunnel` command expects a matching domain assigned to your ngrok account.
Configure ngrok authentication with its normal private supported mechanism; its
token does not belong in deployment JSON or Git. Do not copy the old reserved domain
unless its owner explicitly arranges its authorized use.

`config/google_oauth.json`, initially availability-only:

```json
{"enabled": true, "event_writes_enabled": false}
```

Only change `event_writes_enabled` to true when ready for the separately consented,
approved Calendar write flow. Enabling the flag itself does not grant Google scopes
or authorize an event. Do not disable exact human approval for testing convenience.

`config/brightspace_runtime.json` uses these five exact keys. Example paths are
illustrative and must match your installation; use forward slashes in JSON:

```json
{
  "LMS_MCP_PACKAGE_ROOT": "C:/Tools/AIStudyBrightspace/node_modules/brightspace-mcp-server",
  "LMS_SESSION_DIR": "C:/Private/AIStudyBrightspace/sessions",
  "LMS_TENANT_ORIGIN": "https://your-school-brightspace.example.com",
  "LMS_EXPECTED_ACCOUNT": "your-own-authorized-school-account",
  "LMS_NODE_EXECUTABLE": "C:/Program Files/nodejs/node.exe"
}
```

This runtime file is ignored. It is not an export of a login/session. Actual school
session state must be created by legitimate upstream authentication on your host.
The runtime also supports these settings in the process environment; verify which
configuration the launched bot actually receives without printing account/secret values.

### Telegram and Mini App provisioning

1. Choose the shared abbi takeover or an independent development bot. For abbi,
   coordinate existing-token provisioning privately with its owner and the poller
   cutover below. For an independent bot, use BotFather and your own token. Do not
   revoke/rotate the live token merely to make a clone work.
2. Configure the same token in bot/API so signed Mini App launch data validates.
   Changing tokens requires restarting both processes; never accept unsigned data.
3. Configure the public origin, start the API and HTTPS tunnel, then one bot poller.
   The application sets the Dashboard menu and reads it back for verification.
   `/start` also provides a Dashboard link. Check the actual Telegram menu target.
4. Open the Mini App from that bot in Telegram. An ordinary browser has no valid
   Telegram identity, so private endpoints should return 401. Use synthetic signed
   fixtures for automated tests instead of bypassing identity verification.
5. Test an owner and nonowner through their own accounts. Onboarding/profile UI may
   still say per-student Brightspace is unavailable; the configured owner integration
   is a separate environment-level capability, not public school onboarding.

### IBM Orchestrate provisioning and limits

Obtain authorized access to the hackathon IBM instance and its deployed LIVE agent.
Use the instance URL, key and LIVE agent UUID from that environment's administrator/
supported console configuration. Do not put a watsonx.ai Runtime project ID or key
into the `WXO_*` fields. The current `Configuration` validator specifically supports
the Toronto IBM Cloud host `api.ca-tor.watson-orchestrate.cloud.ibm.com` with
`/instances/<instance-id>`; other deployments require a deliberate compatibility
review rather than editing validation until credentials appear accepted.

The application handles IBM authentication through its normal loader/transport.
No model-facing tool receives IBM credentials, and no remote Calendar tool registration
is needed. Keep the native gateway/provider selected. A successful greeting proves
basic inference only; it does not prove source qualification, tool output formatting,
generation or content review. Log bounded HTTP status/error categories, never raw
provider request/response payloads. Do not automatically retry uncertain inference.

The coding assistant's weekly usage and the application's IBM inference are separate
resources. Reduced coding-agent usage does not explain this stored generation failure.
Actual IBM transport/provider errors need their own evidence; previous synthetic
HTTP 500s are not proof of the latest live job's cause.

### Google project, consent and reconnection

1. Use a Google Cloud project you own or are authorized to operate, with Calendar
   API enabled and an OAuth consent configuration appropriate to its audience.
2. Create/configure a **Web application** OAuth client. The downloaded JSON must
   contain the supported `web` configuration; the service checks it through the
   existing library. Store it as ignored
   `backend/credentials/google_multiuser_client.json`, not as source or a chat attachment.
3. Register the exact redirect URI
   `https://YOUR_ACTUAL_ORIGIN/oauth/google/callback`. The current public origin and
   client redirect must agree. Configure the applicable authorized origin/privacy
   URL for your application. Review `frontend/privacy.html` before reusing its
   original operator contact or outdated feature wording.
4. Use the local Google flags above. Initial consent requests
   `https://www.googleapis.com/auth/calendar.freebusy`. Event management additionally
   uses `https://www.googleapis.com/auth/calendar.events.owned`, separately from an
   individual event's exact approval.
5. Each Telegram student connects **their own Google account** through the official
   external browser flow from Mini App Settings/Connections. Do not ask for Google
   passwords or tokens in Telegram. Read-only consent is enough to test connection;
   do not create real events merely to verify the OAuth callback.
6. After a new OAuth client, hostname, machine or Windows user, arrange legitimate
   reconnection as needed. A copied SQLite grant cannot simply be decrypted by a
   teammate because `WindowsTokenCipher` uses Windows DPAPI plus actor-specific
   entropy. Do not copy another user's Windows profile to work around this.

The original operator reported their own Google consent app as External/In production;
that status is not inherited by a teammate's new project. New users/institutional
accounts may face consent or app-verification restrictions. This review did not
verify fresh-account acceptance, and docs must not imply that it did.

### Brightspace package, session and owner binding

Install the **pinned** `brightspace-mcp-server@3.13.18` in an operator-controlled
location with the required Node/browser runtime. Set `LMS_MCP_PACKAGE_ROOT` to the
installed package directory, not its parent or executable. The loader checks name,
version and `build/index.js`; do not use an unpinned `npx ...@latest` launch.

The upstream session lifecycle may read native/global school configuration, refresh/
persist auth state and open a visible school SSO/MFA browser. Those side effects were
accepted for this PoC. Use your own authorized account and private session root.
Updates are disabled, unrelated bot/IBM/Google secrets are not passed to the MCP child,
and read operations remain allowlisted. Do not resurrect the discarded controlled-
startup redesign merely because an expired session requires upstream reauthentication.

After legitimate authentication, the new environment owner must explicitly approve
binding that developer session to their Telegram owner in their fresh database.
`SourceStore.bind_authorized_developer_session` is an administrative function, not a
public onboarding endpoint. There is intentionally no per-student school-login UI.
An authorized operator may use this application-code pattern **only for their new
own environment**, after verifying its owner configuration and provisioning:

```powershell
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'backend'); import bot; from services.source_store import SourceStore; SourceStore(bot.OWNER_ID).bind_authorized_developer_session(); print('Owner connection bound.')"
```

This is a deliberate local DB write, not an automatic test or permission to modify
the original production binding. It refuses an existing developer-session binding;
do not delete/rebind one to work around it. The `bot` import consumes private config
through the normal application loader without starting polling under its main guard.
Do not print the returned connection ID or inspect protected session files manually.

The owner gate is rechecked on cached reads and queued-job execution. Nonowners
must not see the owner's catalog, notes, source references or generated sets. A
session file alone does not satisfy the owner/connection checks. Cached evidence
is not permission to continue after authorization is revoked.

### Fresh database, persistent storage and process lifecycle

Follow README's new-clone setup. Prefer `scripts/init_database.py` on a brand-new
checkout: it refuses an existing database, creates base/LMS/practice schemas and
checks integrity. `include_practice=True` matters: normal startup intentionally does
not create practice tables. A practice flag with missing tables is not a generation fix.

Persistent paths include `database/study_agent.db`, `.private-lms/`, configured private
school session storage and private configuration. They must be writable and protected
for the chosen host user; never commit/upload them. Temporary school binaries use
private bounded scratch directories and are cleaned up; normal source evidence can
remain in the owner-private cache. Do not treat caching as public content storage.

Start separate foreground terminals, all in the same project checkout:

```powershell
.\.venv\Scripts\python.exe scripts/run.py api
.\.venv\Scripts\python.exe scripts/run.py tunnel
.\.venv\Scripts\python.exe scripts/run.py bot
```

For unattended hosting, configure appropriate supervision manually after acceptance.
Use a stable working directory, persistent disk, private logs and exactly one bot
poller/worker. The host needs outbound IBM/Telegram/Google/school access. Browser
reauthentication may need operator interaction. Do not assume a sleeping laptop,
temporary shell, static GitHub Pages site or ephemeral serverless process hosts
this Python/SQLite/browser application continuously.

Keep the direct deployment on Windows unless a token-storage/runtime port is
explicitly undertaken. A Windows VM/another Windows machine can remove reliance
on the original laptop, but the team must supply/manage that host and authorized
services; this project does not provision hosting automatically.

### Safe cutover of the existing public service

1. Assign one operator and agree whether this is an independent bot or a deliberate
   replacement of the existing service. Review exact code/configuration first.
2. Stage dependencies, origin, OAuth client and authorized new-host state without
   launching a duplicate poller. Obtain separate approvals for production backup,
   record migration or secret provisioning if needed. A source push covers none of them.
3. Check current pending Telegram update metadata without consuming/dropping updates.
   Stop the verified original bot before starting its replacement. Preserve pending
   updates (`drop_pending_updates=False`); never blindly replay uncertain operations.
4. Launch one replacement bot plus its correctly configured API/HTTPS route. Verify
   process identity/startup, Dashboard menu target, public health and signed Mini App
   access. Verify there is no polling conflict and that the practice worker is running.
5. Reconnect Google/school accounts through legitimate flows where required. Test one
   owner and nonowner scenario. Validate user isolation and exact Calendar approvals.
6. Keep rollback source/configuration available; rollback is not a database reset.
   Agree whether to stop the new poller before restoring the old one. Never run both.

No cutover steps are performed or implicitly approved by maintaining this handover.

## Debugging and validation guide for the next AI partner

### Useful files and contracts

| Area | Entry point / contract to preserve |
|---|---|
| Natural conversation | `conversation_telegram.py`, `conversations.py`; private sender, onboarding audience, bounded context |
| Native orchestration | `calendar_native.py`, `native_progress.py`; time/repetition bounds and model-selected tools |
| IBM transport | `orchestrate_native.py`, `orchestrate_client.py`; sanitized metadata, schema checks and no uncertain replay |
| LMS discovery/read | `lms_native.py`, `lms_retrieval.py`, `lms_mcp.py`; owner gate, bounded catalogs, citations and read ranges |
| Source ingestion | `source_store.py`, `document_extraction.py`, `learning_text.py`; owner revisions and consistent excerpt coordinates |
| Practice request | `models/practice.py`, `practice_native.py`, `practice.py:evidence_for/request_practice`; only owned evidence |
| Grounding | `practice_grounding.py:qualify/review`; model interprets scope; Python binds exact source passages |
| Job execution | `practice.py:run_one/worker/generate`; claim once, recheck owner, publish complete set transactionally |
| Attempt state | `practice_store.py`, `routes/practice.py`, `frontend/practice.js`; revision checks, independent saves, private key until submission |
| Calendar review | `pending_proposals.py`, `calendar_actions.py`, `calendar_management.py`, `calendar_telegram.py`; frozen exact approval and actor ownership |
| Identity/settings | `api.py`, `profiles.py`, `google_oauth.py`, `token_cipher.py`; signed Telegram actor and per-user Google grants |

Source selection bounds are currently four learning selections plus up to two
assessment selections; each range is at most 6,000 characters. A request has one to
ten questions, default five; generation batches at most five. These bounds are not
intent routing. The material facade may return up to 60 compact entries within its
serialized bound, and documents expose continuation offsets. Never assume a catalog
or first excerpt represents complete evidence.

Practice jobs are claimed before inference. Running jobs become interrupted on bot
restart; queued jobs remain available. No partial set is published. A student can
explicitly make a new request after failure; do not silently replay the old job.
At most three active and twenty recent daily requests per actor are accepted by
the store. Attempt answers are saved per attempt with revision/conflict handling;
opening another quiz must not overwrite an unfinished attempt.

### Distinguish these failure boundaries

| Observation | What it establishes | What to check next |
|---|---|---|
| `brightspace_access` | Actor/session binding not authorized | Owner/config/binding through approved metadata; never weaken gate |
| `brightspace_unavailable` | Read could not be confirmed; generic adapter/config/external category | Safe schema/startup/transport metadata, not raw session dumps |
| `brightspace_file_limit` | Document exceeds supported bounded file reading | Select smaller relevant content; do not retry same oversized file indefinitely |
| `practice_sources_incomplete` | No job saved; selected evidence insufficient | Read relevant continuation/learning/scope evidence, distinguish bad range from whole missing document |
| Telegram saved/preparing | Request saved | Wait for terminal job status; this is not proof of a valid published quiz |
| `generation_failed` | Worker failed; no exact cause retained | Instrument worker stages before making another speculative fix |
| `generation_uncertain` | Inference outcome uncertain | Do not blindly replay inference/job |
| `generation_interrupted` | Running job invalidated on restart | Avoid publishing partial results; new explicit request if appropriate |
| Static page 200, private API 401 in ordinary browser | Expected unauthenticated boundary | Open through Telegram or use synthetic signed tests |
| Polling conflict | Likely more than one poller for one bot token | Identify verified process roles; stop duplicate deliberately |

Provider HTTP status, model refusal, source insufficiency and failed local validation
are different categories. The Mini App's friendly combined error message cannot tell
them apart. Do not infer the root cause from that text or from old synthetic failures.

### Acceptance matrix to record, not assume

| Check | Expected result | Evidence required |
|---|---|---|
| Owner spontaneous quiz | Actual relevant Brightspace notes produce a complete Mini App set | One real supervised request, final ready status and question/source spot-check |
| Nonowner PDF | Own PDF produces set and private attempt | Live test plus synthetic cross-user denial regressions |
| Isolation | Other actor cannot read materials, sets, attempts, grades or LMS cache | Two-user synthetic API/storage tests, no production student-record browsing |
| Resume/grade | Independent draft survives another quiz; submission returns score/explanations | Save/resume both attempts and inspect only that user's UI |
| Grounded explanation | Conversation teaches supported content with source reference | Actual learning evidence; no invented missing facts |
| Assessment planning | Evidenced test/date and learning scope inform proposed sessions | Source-backed date/scope plus current availability, or explicitly generic plan |
| Calendar revision | Change one proposal session, preserve others, exact approval | User-reviewed proposal and separately authorized real Calendar verification |
| Rejection/idempotency | Reject causes no write; duplicate approval creates no duplicate | Synthetic regression plus bounded approved acceptance |
| Scheduled support | Study-time notification and material-grounded quiz appear | Implemented trigger plus supervised observed delivery, not merely Calendar creation |
| Conflict heartbeat | Added overlap produces one useful alert and alternatives | Supervised real/declared fixture conflict; reject then fresh approve change |

Do not automate real event writes/Telegram messages or inspect live student records
to fill this matrix without specific approval. Use mock integrations by default.
The earlier broad full-suite results are historical; run focused tests for a new
causal fix, then broader tests only when warranted by changed scope/failures.

### Ready-to-copy instruction for an AI programming partner

```text
Read AGENTS.md, README.md and HANDOVER.md. Inspect Git status and the actual current
source before editing. This is a hackathon PoC, deadline 23 October 2026. Preserve
existing changes and authorization boundaries. Do not deploy or access production
records without specific approval. Use synthetic external services/temp databases.

Current blocker: an owner Brightspace spontaneous quiz saves a practice job but the
background worker marks it generation_failed and publishes no set. The historical
underlying exception was discarded. Nonowner uploaded-PDF generation was live-tested
successfully. Do not restart the completed auth/tool-cap/blank-padding investigations.
Do not replace IBM, add keyword routing, revive the excluded coordinator, or loosen
grounding/authorization. No fixed total tool-call cap is wanted; retain time and
repeated-no-progress bounds.

First summarize the code boundary and propose safe constant-stage/error-category
diagnostics for practice.run_one. Make the smallest testable change in isolation.
Test relevant regressions using owned synthetic material. Request explicit approval
before deployment and before any new production-record inspection. Report exact
implemented/tested/live-tested/unverified behavior. If blocked, explain the missing
evidence and leave a small actionable handover; do not burn usage on speculation.
After resolving or explicitly accepting the owner blocker, continue Telegram cleanup,
Mini App mobile polish, and owner/nonowner acceptance. Separately assess missing
scheduled delivery and Calendar conflict heartbeat against the documented demo goal.
```

Do not paste private files into the AI partner. It can use its host's normal loader
for authorized integration tests without printing credentials. A fresh clone may
not contain the original host's local commit graph/worktrees/artifacts; use published
file paths, synthetic fixtures and the evidence ledger here instead of relying on
unavailable local history.

## Publication and documentation checkpoint

The previous local documentation/setup checkpoint is `bdb679a`; the deployed runtime
remains `f76c2d6`. This expanded handover is a later documentation-only revision.
The attempted GitHub update did **not** advance remote `main`: automatic approval
review required exact destination/payload approval. Some unpublished tree/blob
objects may have been uploaded; they are not a branch release. Do not claim a teammate
can clone this new checkpoint until a successful branch update is verified.

The intended destination is the existing private `kiakky/ai-study-agent` repository.
After reviewing the expanded documents and abbi hosting guidance, the developer
authorized updating the existing private repository with the current project source.
Replace obsolete source/docs through an ordinary reviewed
commit while preserving repository history and any unrelated teammate work. Do not
force-push or upload the literal private runtime environment. After these edits,
rebuild the publication manifest: the previous payload/tree is stale.

The intended publication is a current tracked-source snapshot on top of the existing
GitHub history, not an upload of every local experimental branch. Obsolete files
from the earlier sanitized snapshot can be removed from the new tree; their previous
versions remain in history. The current project includes its own dependency list,
fresh-database helper and relevant regression tests. Use the actual published main
commit as the teammate baseline, rather than expecting the local development commit
IDs in this document to be ancestors of that sanitized source snapshot.

Tracked source/tests/docs and safe setup templates are transferable. `.env`, OAuth
clients/tokens, real SQLite, school session files, caches, backups, `.venv`, `.worktrees`
and diagnostic artifacts stay local. Dependency installation and legitimate account
onboarding recreate the independent environment. Keep the repository private unless
the owner separately approves changing visibility and publication checks are complete.

### Documentation and publication validation

This expanded README/HANDOVER should be checked for valid local file/section links,
balanced code fences, parseable JSON examples and configuration names matching the
current strict loaders. There is no need to rerun live IBM or the full application
suite for prose-only changes. Source, credentials and production data remain untouched.
The earlier fresh-clone setup check verified practice table provisioning and refusal
of an existing database using temporary synthetic storage; it did not migrate live data.

Before a later publication, recheck Git status, rebuild/audit the current tracked
file payload, compare the destination branch for teammate changes, and update by a
normal commit with the expected previous head. Do not reuse the earlier partially
uploaded stale tree. Verify the remote branch/commit after publication and give
teammates that exact URL. If automatic approval review rejects an upload, report
its reason and obtain the specified approval; do not change transports to bypass it.

## Safeguards and final acceptance

- Preserve all worktrees and existing edits. Never reset production DB or expose secrets.
- Production record inspection requires **specific approval**. Metadata-only permission
  is not permission to read source text, answer keys or transcripts. Backup/migration
  permissions are separate. Prefer temporary DBs and synthetic integrations.
- No automated real Calendar writes, Brightspace writes or Telegram test notifications
  without explicit approval. Deployment approval is separate from data inspection.
- Calendar operations always need exact user approval; preserve idempotency and recovery.
- A GitHub source update is not a bot release. No new deployment occurred in this final
  investigation. The original live application remains at `f76c2d6`.
- Publish tracked application/tests/docs only. Exclude `.env`, credentials, sessions,
  SQLite, backups, `.private-lms`, `.venv`, `.worktrees` and diagnostic artifacts.
  Preserve the existing private GitHub repository and teammate history; never force-push.

Minimum honest rehearsal: nonowner PDF quiz with resume/submit/review; owner Brightspace
quiz if fixed; two-account separation; evidence-backed assessment planning with exact
Calendar review; scheduled delivery and introduced-conflict adaptation if implemented.
Explicitly label missing features and synthetic demonstrations. Use recorded successful
flows as a fallback and report measured time saved only when actually measured.
