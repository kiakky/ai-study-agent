# Developer handover — AI Study Agent (15 October 2026)

**AI IGNITION 2026 · Problem Statement 2: One Less Thing · Deadline: 23 October 2026**

This document tells the four continuing developers **what exists, what has been verified, what remains, how to test it and how to operate the public application without the original developer's laptop**. Read [README.md](README.md) for installation, credentials, starting independent copies, links and configuration. This is a sanitized **source-only handover**, not a copy of the original Windows account, databases, user records, credentials or live deployment. Python remains the application/tool owner and IBM watsonx Orchestrate remains its reasoning provider. Do not rebuild the entire application inside IBM.

## Action plan for the four teammates — begin here

**Handover date:** 15 October 2026. **Submission deadline:** 23 October 2026.
There is very little integration and presentation time. Read this section before taking
on an enhancement, and coordinate changes through reviewed GitHub feature branches.
This is an assignment **proposal**, not a claim that owners have accepted the tasks.

### Within the first hour

1. Each teammate accepts the repository invitation, clones `main`, reads README's
   30-minute quick start, runs the safe offline setup/tests and reports any setup blocker.
2. Agree one named owner per lane below, the test environment for each lane, and interfaces
   before changing shared Python modules. Put the names and status in your team tracker.
3. Check the deployment reality: the live laptop uses source `5c28756`; the published
   repository additionally includes small navigation changes **not deployed**. A cloned
   repository has no live credentials or user records. Coordinate deployment with the
   original operator; do not assume a `git push` updates the live bot.
4. Start presentation/deck/video planning **in parallel**, not after engineering. Confirm
   submission format, repository sharing rules, judging dates and IBM usage requirements
   directly with the organizers. This document cannot certify requirements not checked.

### Live-host ownership and migration — read before the original laptop goes offline

**A repository clone is source code, not a running deployment.** Choose one named
**judge-demo operator** to keep the public app available throughout the stated judging
window. This can be a teammate's laptop, but it must stay powered on, awake, networked,
and run **one FastAPI process, one ngrok tunnel, and one Telegram polling bot process**.
Google Cloud OAuth *In production* does not host these processes. The original operator
can stop their laptop only after the replacement is confirmed online; otherwise the
published homepage, Mini App, chat responses and OAuth callback may stop working.

**Two supported paths — agree which one applies:**

| Path | Telegram bot identity | Web address, Google and data | Use case |
|---|---|---|---|
| **Independent developer clone** | Create a separate bot with **your own** `TELEGRAM_BOT_TOKEN` and owner ID in private `.env`; never reuse the live token. | Create a new private SQLite DB, your own ngrok account/domain, IBM environment and Google OAuth client/project. A new Google Cloud project does not inherit the existing project's Production status. | Parallel coding, own-account integration tests, synthetic demos. Does **not** keep `@AIStudyAgentTPBot` alive. |
| **Take over existing public bot** | A **single designated operator** uses credentials legitimately transferred by the original owner through a secure channel, or coordinates an authorized BotFather ownership/credential transition if supported. The SAME bot token identifies the SAME Telegram username and link. Only **one** poller worldwide may use it at any time. | Coordinate the existing ngrok account/domain **or** replace the HTTPS origin and update every dependent configuration/Google OAuth client URI. Create fresh account-bound data/consents rather than copying private developer state. | Judge-facing continuity of `https://t.me/AIStudyAgentTPBot` after the original laptop is stopped. |

**Do not assume the existing ngrok hostname moves to a teammate's account.** That
hostname is associated with the original ngrok account. To keep the exact existing
hostname, the authorized account operator must run its tunnel on the replacement host;
**never** paste ngrok authentication tokens into Git, chat or screenshots. If the
replacement uses a different hostname, coordinate all of the following **before**
stopping the current public host:

- Set the new private `config/deployment.json` `mini_app_url` and allowed host to the
  exact new HTTPS origin. Start the new API and ngrok tunnel on that host and check
  public `/`, `/privacy`, `/health`, signed Telegram Mini App access and private-route denials.
- In the appropriate **Google Cloud project and Web OAuth client**, add/update the exact
  authorized redirect `https://NEW-HOST/oauth/google/callback` and update Branding
  homepage/privacy/authorized domain as required; verify Google's actual consent and
  callback with the new origin. Existing Google OAuth Production status belongs to the
  original project, not a newly created one. Do not share personal Google login
  credentials; use proper project permissions for a designated maintainer.
- Update Telegram's Dashboard/Mini App menu button URL and verify `/start` on a real
  account launches the NEW origin. The username `@AIStudyAgentTPBot` remains unchanged
  only if its original bot identity/token remains the one being operated.
- Use new local SQLite records and have students reconnect their **own** Google accounts
  unless a separate, explicitly approved and privacy-reviewed data migration exists.
  Existing OAuth tokens use Windows DPAPI and the original Windows identity, so a simple
  `.db` copy to another computer will **not** make connections transferable. **Never
  transfer Brightspace Microsoft SSO/cookies/session or private course data.**

**Safe cutover checklist for the same public Telegram bot:**

1. Original operator and incoming operator agree a short maintenance window, assigned
   hosting machine, private credential provisioning, rollback contact and judging coverage.
   Do not post bot tokens, IBM keys, client JSON, ngrok tokens or DB files to GitHub.
2. Stage the replacement's code/dependencies/config with **its own** account data. Test
   FastAPI/ngrok first without starting a second live Telegram poller. Preserve explicit
   approval, actor identity and source read-only boundaries. Do not expose a synthetic
   preview server or private admin port publicly.
3. On the original host: assess pending consequential bot updates without consuming,
   discarding or advancing Telegram offsets; create a verified private SQLite backup;
   stop the **original** bot poller before the replacement bot starts. If switching web
   hosts, coordinate the API/tunnel and Google redirect/menu cutover as above.
4. Start **exactly one** poller for the original bot token on the designated host.
   Verify `/start`, a real greeting, a signed Mini App session, isolation between users,
   Google OAuth from an unlisted account, the actual callback, public privacy URL and
   an intentionally reviewed action. Test rejected/expired approvals and no accidental
   writes. The two-tester conversation gate is still a separate release blocker.
5. Confirm the actual public bot, Mini App, homepage and privacy URL remain accessible
   from a different device/network. Monitor logs for polling conflicts, OAuth redirect
   mismatch, ngrok warnings and provider failures. **Only then** retire the old
   API/tunnel and let the original laptop go offline. If replacement fails, restore
   the original single-poller deployment safely; never run both simultaneously.
6. Keep the assigned host online for the entire judging period or explicitly document
   scheduled availability. A video demo is an honest fallback, not a substitute for
   a live endpoint when judges expect hands-on access.

**Start commands on the designated Windows host** (from README; separate terminals,
with fresh per-host credentials and configuration):

```powershell
.\.venv\Scripts\python.exe scripts/run.py api
.\.venv\Scripts\python.exe scripts/run.py check --local
.\.venv\Scripts\python.exe scripts/run.py tunnel
.\.venv\Scripts\python.exe scripts/run.py check
# Start ONLY after verifying no other poller uses the same Telegram bot token:
.\.venv\Scripts\python.exe scripts/run.py bot
```

**Who owns what after handover:** record one person for (a) live bot/token custody and
polling; (b) ngrok HTTPS origin; (c) Google OAuth project/client and authorized redirects;
(d) IBM Orchestrate credentials and LIVE agent; (e) uptime, consent/security checks and
rollback; and (f) judge-facing account tests. Transfer only the minimum required access
through an approved private channel. If nobody owns deployment, **the GitHub repository
alone will not keep the public project online**.

### Recommended ownership and release priority

| Priority | Suggested owner/lane | Minimum deliverable and acceptance evidence |
|---|---|---|
| **P0 — judge access** | Teammate 1: Telegram/Calendar | A **third, previously unlisted** Telegram user can chat with Orchestrate and use their own Calendar without borrowing admin rights. Remove audience gates only through an explicit policy, preserve signed identity, user isolation, approvals and rate bounds. Test the existing two users still work. |
| **P0 — Calendar bug** | Teammate 1 | Reproduce `"next two weeks"` → `"2 a week should be enough"`; inspect sanitized invalid-action metadata, fix the schema/context cause, add a regression, then demonstrate a complete proposal → amendment → exact approval → own Google event → reschedule/cancel. No guessed success messages or unlimited tool loops. |
| **P0 — external Google OAuth check** | Teammate 1 with a second volunteer | Use a Google account **not** on the former test list and a separate Telegram identity. Verify consent and callback, note the unverified warning/100-user cap, and do not reveal the person's token or calendar. Publishing status is already *In production*; this test is not yet passed. |
| **P1 — academic evidence** | Teammate 2: Brightspace | Use their **own authorized** SSO session and the existing strictly read-only MCP/evidence code. Establish per-student binding and demonstrate a course/exam/deadline or a clearly labeled synthetic fallback with provenance and no cross-user leakage. No writing/mark-read/submissions. |
| **P1 — learning practice** | Teammate 3: quizzes/weakness memory | Generate a small validated quiz grounded in authorized retrieved evidence or clearly labeled synthetic coursework; show source references, saved score/errors and a targeted follow-up recommendation in the existing Mini App. Existing demo MCQs alone do not meet this. |
| **P1 — autonomous recovery** | Teammate 4: monitoring and demo | With consent, detect one meaningful change in a verified/synthetic exam deadline, explain before/after evidence, propose changed sessions and seek explicit approval. Reject leaves Calendar unchanged; notifications are deduplicated. Own the initial slide/video/demo evidence and time-saved measurement plan in parallel. |
| **P2 — polish** | Everyone after P0/P1 | Better interface labels, nonessential automation, extra course types and deployment packaging **only if** the critical end-to-end demo and submission deliverables are already safe. |

The four teammates should own these lanes; the original operator is not the default
implementer of every remaining feature. If the P0 calendar/public access path cannot be
finished, **flag it early**, do not pretend that the judges have unrestricted access, and
record a stable, labeled video of verified functionality for the submission.

### Interfaces and dependencies — agree before parallel coding

- **Academic evidence → reasoning/quiz/monitoring:** Design an actor-owned source reference
  with course/document identity, retrieval time, source revision or hash, text segments,
  source citation and an *unknown/unverified* state. The existing `lms_retrieval.py` and
  `source_store.py` own this foundation. This is a conceptual contract; verify actual
  models and migration needs in code rather than treating this description as a schema.
- **Quizzes → learning memory:** Save each question's evidence reference and source revision,
  answer key, explanation and per-user attempt/mistake outcome. An LLM's plausible answer
  must not replace source verification. Use the existing grades/results interfaces.
- **Monitoring → planning:** Compare authorized source revisions, emit one idempotent
  meaningful change (including a before/after citation), and request a revised proposal.
  Never write to Brightspace or silently alter Calendar events; reuse the existing human
  approval pipeline and Python Calendar executors.
- **Identity across lanes:** Derive the Telegram actor from verified updates/initData;
  external account/session bindings are per actor. No shared developer tokens or owner
  shortcuts. All new persistence needs migration review and actor-isolation tests.

### Minimum judge-facing acceptance scenario

1. New judge/test student opens [the bot](https://t.me/AIStudyAgentTPBot), uses their own
   identity, and sees onboarding/settings. The public laptop host must be online.
2. Agent discovers an exam/course/deadline from that student's authorized, read-only
   Brightspace evidence **or an explicitly labeled synthetic fixture**; state which source
   is real and cite it. If safe LMS login is absent, do not expose developer coursework.
3. Student asks naturally for revision sessions; the agent uses preferences and their own
   Calendar free/busy, proposes exact sessions, accepts one conversational correction,
   requests exact approval and confirms only actual Google results.
4. Student completes a course-grounded quiz. Scores and missed concepts persist and inform
   a follow-up suggestion. Clearly separate this from preloaded demo MCQs.
5. One changed exam deadline/announcement is detected from an authorized or synthetic
   source. The agent explains the change and requests fresh approval before rescheduling;
   rejection must not modify existing events.
6. Show **evidence of time saved**: time the same manual workflow (clearly state which
   steps were compared), record timestamps/steps for an agent run, and report actual
   measurements with limitations. Do not fabricate comparison data or imply the agent
   performed a step that was simulated.

If not every step passes, demo the verified subset, mark synthetic or incomplete steps
visibly, and report remaining defects. A video is a fallback, not proof the public bot
works for unknown users. Verify all final links/permissions from an external account.

### PR/deployment handoff checklist

- Explain what changed, which existing module/interface is reused and what remains undone.
- Show targeted mocked tests, actor-isolation/permission-denial cases and one supervised
  integration test where safe. Do not run real Calendar writes automatically.
- Update README's capability-status table and this document's owner/progress notes if the
  feature has actually been tested; avoid carrying forward obsolete 'pending' claims.
- Never commit `.env`, tokens, client JSON, school sessions/materials, user DBs, downloads,
  backups, logs or sensitive historical Git objects. Run `python scripts/audit_publication.py --history`
  and review diffs before a PR merge.
- For the operator's running laptop, obtain approval for reviewed code, a verified private
  SQLite backup, a non-consuming queue check, one bot/API restart and live acceptance.
  Publishing a GitHub branch is **not** deploying it.
- Maintain a short blocker/status log: owner, branch/PR, tested result, outstanding risk,
  demo readiness, and responsible teammate. Do not silently drop an unfinished milestone.


## Start here: evidence, defects and blockers

| Area | Actual state, not aspirational |
|---|---|
| Telegram / Mini App | Original operator's laptop runs the bot/API/ngrok. `/start`, names, bounded conversation context and Mini App demo quizzes/settings/results have some real user evidence; source clone has no live state. |
| Orchestrate | LIVE `AI Study Agent - POC` agent 1.0.0 uses Groq GPT-OSS 120B. Python IAM exchange, LIVE metadata, streaming transport and parsed replies have been verified. The old watsonx.ai Runtime 300,000-token monthly quota was exhausted; Orchestrate is the chosen active provider. |
| Calendar | Python implementations exist for per-user free/busy, proposals, pending changes, exact consent, event creation/move/cancel and idempotency. The Orchestrate bridge is built but live conversational scheduling is not fully verified. |
| **Calendar bug to fix** | After 282 passed offline regressions on repair `2414915`, real chat still failed: request for machine-learning sessions over two weeks → frequency question → “2-3 for each week should be enough” → repeated question → “2 a week should be enough” → **invalid tool request**. Account A previously went silent; Account B hit the bounded tool-call limit. Preserve safety guards while fixing this. |
| Google OAuth | Original operator's Google Cloud project is External/**In production** as of 15 October. No full Google verification. Unverified warning/applicable new-user cap accepted. **Unlisted-user full OAuth consent/callback untested.** |
| **Public Telegram gate** | Public Telegram username, `/start`, Mini App entry ≠ public LLM conversation. Current configured access to conversations/Calendar session controls still limits to two testers. A third account is required for acceptance. |
| Brightspace | Read-only MCP allowlist/extraction/source store, versioned evidence and owner binding available; **not** independent per-user school SSO, deployed student-specific LMS reasoning or guaranteed exam coverage. Do not use the original operator's personal school data. |
| Quizzes | Static demonstration MCQs, grading, stored attempts and results exist; evidence-grounded generation/weakness analytics not yet implemented end to end. |
| Monitoring | No general proactive course-change watcher, personal replanning loop or time-saved measurement implementation. Consent/outbox cleanup or bot heartbeat alone does not meet this milestone. |
| Navigation/hosting | New public-site Telegram links are present in the GitHub snapshot but were **not deployed** by the original handover. One API, one ngrok tunnel and one Telegram bot are required on a maintained host. |

The latest sanitized publication checkpoint was reported pushed on GitHub `main` at `afb882d`; verify the branch/revision before making edits. The source-only handover is based on live code `5c28756` plus static navigation changes. Do not confuse a newer GitHub documentation commit with a running deployment. See `SECURITY_AUDIT.md` for the original content/history audit and publication-focused test results. The 282-test Calendar figure describes an earlier mocked test run, **not** complete external acceptance or a fresh suite on every clone.

## Original product vision — full end-to-end workflow

The primary goal is an **autonomous AI exam preparation agent**, not a generic question-answer bot:

1. **Onboard:** Student starts Telegram bot; personal profile, preferences, strengths/weaknesses and study constraints belong to the authenticated Telegram actor.
2. **Connect:** Student optionally authorizes **their own** Google Calendar and Brightspace school account. No shared developer login or credential fallback.
3. **Discover:** Strictly read-only Brightspace MCP lists allowed courses/materials, announcements, exams and deadlines. Source revisions, dates, citations and uncertainty are stored per actor.
4. **Prioritize:** Orchestrate reasons over authorized academic evidence, deadlines, known weaknesses, availability and user instructions; distinguishes recommendations from facts.
5. **Plan:** Python gathers the student's actual Calendar free/busy and saved soft preferences, calculates valid exact local sessions and returns a proposed revision timetable.
6. **Negotiate naturally:** Student can say “two per week,” “not Tuesday,” or “move just Friday to 3 PM.” Orchestrate should retain the original request, ask only genuinely missing questions and produce validated **internal** action requests; the student never types JSON.
7. **Approve:** Python presents the *complete exact proposal*. Amendments invalidate stale approvals. Consequential event creations/changes happen only after a specific explicit human approval.
8. **Execute:** Python writes only authorized events to the correct student's **primary** Google Calendar, guarding conflicts, ownership, state changes, duplicate actions and retries. Rescheduling/canceling use separate approvals.
9. **Practice:** The Mini App presents exam-relevant, source-grounded generated quizzes with appropriate difficulty, answer key, explanations and supporting evidence.
10. **Remember learning:** Scores, missed questions and weak-topic summaries persist separately by student, influencing next practice/revision recommendations.
11. **Watch for changes:** With opt-in and reasonable cadence, the backend checks permitted course data for *meaningful* changes such as new exams or deadline amendments. It must not acknowledge, submit, mark-read or alter Brightspace content.
12. **Recover:** Agent compares before/after sources, explains the changed plan, obtains approval before touching already scheduled Google events, and sends a useful Telegram notification.
13. **Prove benefit:** A demo tracks manual effort compared with the actual agent workflow, showing steps/time saved and the changed-deadline recovery case. Never invent numbers or claim a simulated operation is real.

This is the desired final behavior. The known gaps above remain. The AI IGNITION PS2 solution must show a meaningful completed task, two or more integrated tools, recovery from a change and authorization at the right moment. Demonstrate the strongest **verified** complete workflow rather than breadth with untested claims.

## Architecture, responsibility and specific code-entry guide

**Auth chain:** Telegram verified sender / signed Mini App `initData` → actor-bound Python services/SQLite → provider factory → Orchestrate LIVE reply → strict validated JSON action request → actor-scoped Python Calendar tool → review/proposal → human confirmation → approved Python executor. Brightspace information must enter the same actor-scoped authorization and evidence boundary. The external LLM can *reason and request*, never select an arbitrary student identity, approve itself or execute unrestricted tools.

| Responsibility | Where to begin |
|---|---|
| API / Mini App | `backend/api.py`, `backend/routes/`, `frontend/`. Public static pages and protected student endpoints must stay separate. No forged Telegram `initData` bypass. |
| Telegram bot | `backend/bot.py`, `conversation_telegram.py`, `calendar_telegram.py`. Exactly one poller per token. Preserve delivery retries, approval messages and service health. |
| Memory and persistence | `backend/database.py`, `backend/models/`, `conversations.py`, profile/quiz/planning services. New persistence needs actor ownership, schema review and additive migration. |
| IBM reasoning | `llm_providers.py`, `orchestrate_client.py`, `orchestrate.py`, `orchestrate_actions.py`; `docs/orchestrate-provider.md`. LIVE IAM/streaming parser, allowlisted action contracts and one bounded repair. |
| Calendar | `calendar_agent.py`, `calendar_tool_inputs.py`, `pending_proposals.py`, `planning.py`, `scheduling_core.py`, `calendar_conversation.py`, `calendar_actions.py`, `calendar_management.py`, `calendar_text_decisions.py`, `calendar_change_decisions.py`, `calendar_telegram.py`. Reuse existing approvals/outbox and validation; don't rebuild a second executor. |
| Google auth | `google_oauth.py`, `token_cipher.py`, private JSON example and OAuth callback. PKCE/state, token encryption, connection generations, scopes and consent. |
| LMS | `brightspace.py`, `lms_mcp.py`, `lms_retrieval.py`, `source_store.py`, `document_extraction.py`. Read-only tool allowlist, owner bindings, revisions, evidence and extraction. |
| Testing / operations | `tests/`, `scripts/run.py`, `scripts/init_database.py`, `scripts/audit_publication.py`, `scripts/check_orchestrate.py`, `config/*.example.json`, `.env.example`, `SECURITY_AUDIT.md`, `AGENTS.md`, `docs/`. |

Relative module labels may be under `backend/services/`; look at the actual file before changing import paths. Consult specialized documents under `docs/` before rewriting an existing interface. Avoid an unnecessary Docker migration, new database, whole-backend Orchestrate rebuild or frontend-framework conversion with eight days to submission.

### Security / approval invariants — do not compromise

- **Identity:** Only Telegram's verified sender or signed Mini App `initData` determines the actor. Neither LLM JSON, Google email, URL params nor model prose may provide authority. Every relevant DB query/action must be actor-scoped. Legacy owner-only `/schedule` is not a permission model for all students.
- **Orchestrate:** Remote agent tool/memory/hooks/knowledge capabilities are deliberately disabled; fresh remote threads replay bounded *authorized* local context. Envelopes use a strict local allowlist; unknown operations/fields and identity claims fail closed. Malformed output gets at most one bounded format repair. The LLM cannot request human approvals or Calendar execution directly.
- **Review/consent:** Exact proposals include titles, aware timestamps, owners, digest/review version, expiration and account connection generation. Modification ≠ authorization. Expired/rejected/older controls must never execute; approvals must be durable and idempotent. One action shouldn't silently switch Google accounts.
- **Google:** Free/busy + approved `calendar.events.owned` on that student's own primary Calendar; Python rechecks authorization, conflicts, ETags and idempotency. Consent alone creates no event. Only Python-confirmed results justify “created/changed/cancelled” claims.
- **Brightspace:** Strictly read-only. Deny unknown/submit/write/mark-read/acknowledge operations, untrusted output paths and prompts embedded in retrieved documents. The user's own legitimate session is required. The original personal school SSO must never be shared or transferred.
- **Storage/privacy:** OAuth tokens use Windows DPAPI tied to the Windows account and Telegram actor. This does not encrypt every SQLite field and is not portable to another host. Never publish tokens, data, course documents or history. Deletion is currently operator-managed; do not claim automatic deletion or provider-side erasure.
- **Reliability:** Bound model/tool loops, Telegram delivery, retries and monitoring; uncertain external actions require reconciliation, not blind replay. Maintain separate approval and network side-effect logs without spilling secrets.

## Priority P0 — safe public onboarding and Calendar repair

### 1. Public Telegram/Orchestrate/Calendar audience

Current restrictions were intentionally limited to two testers. Relevant starting points are `calendar_testing.configured_testers()` / ignored `config/calendar_test.json`, `conversations.load_settings()` (`audience: calendar_testers`), `conversation_telegram.handle_text`, preference/clear-history handlers, and `calendar_telegram.session_list`/session callbacks. `OWNER_ID` must remain reserved for legacy admin-only functions. A third student should be able to use normal conversations **without** assuming owner rights.

**Implementation:** Design explicit default-off public-audience policy, adapt each entry point consistently, maintain signed-identity checks, personal SQLite context, provider quotas/per-user pacing, capacity bounds, exact reviews, and cross-user data denials. Do not just delete a tester-ID comparison and grant administrative shortcuts.

**Acceptance:** Unknown Telegram account can `/start`, access signed Mini App, converse with IBM Orchestrate, connect its own Google account and request/review/manage only its own sessions. Both existing testers keep their data. Invalid actor tokens, forged IDs, another student's resource IDs, stale approvals and excess traffic fail safely. Verify with a third unlisted Google account and accept the documented unverified-app warning where permitted. Google OAuth *In production* does not accomplish this audience change by itself.

### 2. Scheduling failure: follow-up clarification

Reproduction from live October 15 Telegram test:

```text
Student: Can you help me set up study sessions for the next 2 weeks?
         For studying machine learning.
Agent:   How many study sessions per week would you like over those two weeks?
Student: 2-3 for each week should be enough.
Agent:   [Repeated the same question]
Student: 2 a week should be enough.
Agent:   The model returned an invalid tool request. No action was approved; please try again.
```

The preceding deployment also showed Account A going silent and Account B exhausting the safe tool-call limit; `2414915` improved timeouts and initial frequency prompts, but the quoted follow-up error was **still present**. Do not assume this is caused by “weak prompting” before inspecting the actual rejected JSON action, schema, pending clarification state and Orchestrate tool feedback. Preserve LLM natural-language understanding, let Python calculate exact dates/validate Calendar results, and reuse existing proposal/approval tools. **Do not simply increase the tool-call budget or execute model prose.**

**Minimal fix process:** targeted sanitized logs → exact reproduction under synthetic two-user context/mocked Calendar → identify validation or memory mismatch → smallest schema/intent/instruction repair → focused offline regression → one bounded synthetic Orchestrate test if needed → supervised real Telegram acceptance with the operator's consent.

**Acceptance:** “two sessions a week for two weeks” produces **four** proposed sessions subject to personal availability, without a repeated question. “2-3 a week” should be interpreted reasonably or clarified briefly once. An individual pending-session amendment preserves other sessions; explicit approvals create correct events in each student's own Google Calendar, then an individually approved move and cancellation work. Test temporary overrides, duplicate/expired approvals, no-write rejection, provider failure and cross-user denial. Real writes require deliberate user approval; never automate them during debugging.

### 3. External Google OAuth judge test

Publishing status on the original Google project is **External / In production**, but no full OAuth consent/callback test from a new unlisted Google account has been observed. Use a third Telegram identity and Google account not on the old test list. Enter Mini App → Settings → Connections → Connect Google Calendar, go through the unverified warning if accepted, validate callback to the correct stable HTTPS origin and verify the new user's account is connected **only to them**. No actual event creation is needed to test OAuth. An unverified warning, potential new-user cap and institutional Google account restrictions remain limitations; any failures must be documented honestly.


## Priority P1 — read-only Brightspace with student-owned sessions

Read `lms_mcp.py` and `lms_retrieval.py` first. The existing infrastructure uses a positive tool allowlist, `readOnlyHint: true`, an isolated subprocess environment that strips unrelated secrets (including `WXO_*`), disabled downloads until verified, and source revisions/evidence extraction. The pinned external package is `brightspace-mcp-server@3.13.18`, with update checks disabled. Existing `SourceStore` requires a verified owner and explicit `developer_session` binding, **not a production-ready per-student account/session connection**. Supported extraction foundations include text, HTML/Markdown, text PDFs, DOCX and PPTX; scans/diagrams/complex tables are not guaranteed.

1. Investigate the existing MCP wrapper rather than rebuilding it. Inspect available read-only tools for course listing, resources, exams, announcements, retrieval and revision comparison. Record which capabilities the actual authenticated institution account exposes.
2. Authenticate the teammate's **own** school account through legitimate browser SSO/MFA in an isolated local session (or use the school's supported sanctioned integration). Never transplant the original operator's Microsoft SSO browser profile or cookies. Ask no student for login credentials through Telegram.
3. Before permitting public student retrieval, implement Telegram-actor → verified Brightspace account/session binding, isolated browser transport and owned retrieval/cache paths. Handle expired sessions, logout and reauthorization. Do not share a global developer MCP child process/session across students.
4. Maintain a strictly **read-only** tool allowlist. No assignments, submissions, acknowledges/mark-read, grades writes, impersonation or arbitrary file-system destinations. Treat retrieved materials as untrusted data, not assistant instructions.
5. Persist source identity, course name, document revision/hash, retrieval timestamp, relevant text segments and citations where authorized. Distinguish confirmed exam dates from incomplete or ambiguous notices. If retrieval is inaccessible because of institutional restrictions, use explicitly labeled synthetic school fixtures or authorized student-provided documents; no invented exams.

**Definition of done:** Two distinct own-account students retrieve only their own courses and documents; cross-account reference/session paths are denied; expired login requires reauthorization; no LMS write tool can execute. Demonstrate one evidence-backed exam date/announcement (or clearly labeled synthetic fallback), preserving provenance and uncertainty. Do not call owner-only MCP experiments public student integration.

## Priority P1 — source-grounded quizzes and learning memory

The Mini App currently has quiz attempts/results and saved grades. Avoid replacing its UI or recreating the attempts subsystem.

1. Use authorized Brightspace/extraction evidence (or clearly labeled synthetic course fixtures) as the source for GPT-OSS quiz generation. Generate bounded, typed questions, answer choices, correct answers and explanations that cite or link back to document segments/revisions.
2. Apply validation: no blank/duplicate answers, valid correct-answer indexes, appropriate difficulty/format, source grounding, and uncertainty/refusal when content is insufficient. An LLM-generated answer may be plausible and wrong; review at least a representative answer-key sample against cited source material.
3. Save course/topic/source revision, quiz parameters, student responses, grades and missed-concept summaries under the correct verified Telegram actor. Reuse current Mini App quiz, resume, grade history and retake behavior.
4. Derive compact per-student weakness information from missed topics and completed quizzes. Provide future revisions and quiz recommendations using this evidence. User-supplied one-off preferences are soft task overrides; do not silently overwrite permanent settings.
5. Verify persistence after backend restart, different users' grade isolation, deliberate retakes, correct scoring and a focused follow-up quiz on a previously missed concept.

**Definition of done:** A small evidence-backed generated quiz is served on the Telegram Mini App, the student completes it, grades and explanations persist, weak topics are recorded appropriately, and a later suggestion reflects missed material. A preloaded generic demo quiz alone is insufficient; clearly mark any synthetic source as synthetic.

## Priority P1 — autonomous monitoring, change recovery and notifications

This milestone is central to the hackathon PS2 “One Less Thing” requirement. Existing bot heartbeat, approvals and outbox maintenance are **not** a general academic watcher.

1. Obtain user opt-in for relevant LMS checks, monitored courses and notification preferences. Create a bounded, durable periodic job or an explicitly supervised demo trigger that survives/reconciles restarts without duplicate work.
2. Compare previously authorized source revisions to new Brightspace announcements/exam data or well-labeled synthetic changes. Ignore cosmetic changes; detect meaningful new exams/deadline changes with source-based before/after evidence.
3. Connect a verified change to the current study goal and Calendar plan. Let Orchestrate reason about an updated recommendation; Python validates exact new sessions, availability, identity and temporal constraints.
4. Send one useful Telegram change notification with provenance. Proposed Calendar modifications require a **new explicit review** of the exact selected sessions. Rejected modifications leave existing events untouched.
5. Bound polling frequency, provider usage, error retries, quiet hours and notifications. Do not silently mark announcements read, alter LMS records or bypass a student's revoked session.

**Definition of done:** A changed exam/deadline is detected once → one explanation/notification → revised plan presented → rejection makes no Calendar write → fresh specific approval changes only authorized events. A retry/restart does not duplicate notifications/events. Demonstrate provider/SSO failure safely; synthetic academic changes must be conspicuously labeled.

## Final integration, presentation, video and submission

**Start deck and video planning now, in parallel with code.** The deadline is 23 October 2026; official formats, file limits and exact submission details must be checked directly with the organizers. The previously discussed judging period may extend after the deadline; assign a live-host operator for any required access rather than assuming judges will clone and run the repository.

Recommended final demonstration (use source-backed or clearly marked synthetic exam fixtures):

1. A fresh Telegram user opens the bot, creates a profile and selects preferences.
2. They connect **their own** Google Calendar; show the consent flow with the unverified warning limitation if applicable. For Brightspace, show only their own authorized courses or a declared fixture.
3. Agent discovers a real/fixture exam or deadline, cites its source and proposes sessions aligned to student availability.
4. Student changes exactly one session conversationally and approves the specific updated proposal. Verify a real event appears in the correct Calendar, without duplicate creation.
5. Student completes a grounded quiz in the Mini App; saved grades and a weak-topic recommendation are shown.
6. A previously authorized announcement or exam deadline changes; the agent finds the difference, explains it, sends a bounded notification and requests explicit approval to replan. Reject once to demonstrate the Calendar remains unchanged, then demonstrate approved recovery if safe.
7. Present **measured time saved**. Define the manual baseline (e.g., discover an exam, compare Calendar, select sessions, enter Calendar events, adjust after changed deadline) and measure both workflows; show step counts/seconds and limitations. Do not fabricate savings or equate an LLM's narration with tool execution.

**Judging readiness:** Public bot/hosting uptime, Google OAuth, Brightspace data isolation, operator contact, source availability, functional video, presentation deck, architecture/dataflow diagrams, hackathon PS2 mapping, signed-account demos, code access and privacy contact. Verify all provided links as an unknown external user. If a subsystem remains unsupported, mention it accurately and use the demo video as a fallback; do not claim the judges can independently perform a blocked step.

### Team work and integration rules

- Assign named owners for Calendar/public access, Brightspace, grounded quizzes, and monitoring/demo. Assign one operational owner for the live Telegram/ngrok/Google/IBM stack, with a backup contact. These are **suggested roles**, not proof someone agreed.
- Use independent feature branches, reviewed pull requests and bounded interfaces. Agree on source-evidence schema and actor identity before parallel edits. Avoid direct modifications to common bot routing without coordination.
- Run mocked tests first, including denials for forged cross-user resource IDs, duplicate/expired approvals, and prohibited Brightspace writes. Run limited supervised live tests only with deliberate user consent. Do not spray real exam/Calendar data into logs or providers.
- Update README's capability table when a feature passes. Distinguish *implemented*, *mocked-tested*, *live-tested*, *blocked* and *planned*. Preserve known failure records for work not finished.
- Run `scripts/audit_publication.py --history` and review the full diff/history before publishing changes. Do not accidentally commit local `.env`, token files, student DB, browser profiles or school course content.
- A GitHub push is a code update, **not** a release to the operator's laptop. A live deployment must be planned and approved with fresh verified SQLite backup, non-consuming Telegram queue check, exactly one polling process, stable ngrok and proper post-deploy real-user health checks. Never discard unknown pending consequential updates automatically.

### Handover status record (team should maintain)

| Workstream | Responsible teammate | Branch/PR | Verified result | Open blocker | Next check |
|---|---|---|---|---|---|
| Judge/public access and Calendar | **Assign** | — | Existing foundation, two-tester gate | Open scheduling clarification / public enablement | Third-account acceptance |
| Read-only Brightspace | **Assign** | — | Owner-bound read-only foundation | Independent account isolation and evidence grounding | Own-school read-only proof |
| Generated quizzes/learning | **Assign** | — | Demo quiz/grade UI and storage | Source-grounded generation/weakness memory | Generated quiz and persisted result |
| Monitoring/replanning | **Assign** | — | Approval/notification plumbing foundations | Actual changed-evidence watcher | Change → alert → new reviewed proposal |
| Demo/video/slides/submission | **Assign** | — | Initial plan | Confirm organizer deliverables and measured evidence | Recorded verified demo |
| Public host and credentials | **Assign** | — | Original laptop currently hosting | Handover/cutover, uptime, service ownership | External-network health + fresh Google OAuth |

## Operations and incident response

The original live deployment is a Windows laptop running one FastAPI API on loopback, one ngrok tunnel and one Telegram polling process. T3 Code/VS Code are not runtime dependencies. The original website, bot's Mini App and Google OAuth callback are unavailable when that host or its tunnel is offline. Data remains on that host unless a separately authorized data migration exists. **Copying the Windows SQLite DB to a teammate does not transfer DPAPI-encrypted Google grants.** Do not transfer the original user's Brightspace login/session or course content.

For a new teammate clone, follow README setup and `scripts/run.py` commands with a **new bot, new database and own service accounts**. For the existing public bot, follow the detailed live-host cutover instructions above; coordinate first because the bot token/public identity, ngrok reserved hostname and Google redirect must be operated consistently. Two processes polling one Telegram token produce conflicts and/or missed replies. If the current host is to be retired, stage the replacement without a second poller, back up data securely, check pending updates non-destructively, stop the original poller, start the new single poller, update configured website/callback/menu if needed, and validate real accounts from a separate device. Only then allow the original host to stop.

**Avoid these unsafe “fixes”:** resetting or copying production SQLite, disabling signed Telegram identity checks to let judges in, sharing Google/IBM/bot/SSO tokens over chat or Git, raising infinite action loops, activating Brightspace writes, switching OAuth clients without matching redirect and user reauthorization, restoring backups blindly, or running two bot pollers. Follow the exact approval and reconciliation state before retrying Calendar writes with uncertain outcomes.

If a failure occurs, inspect sanitized operation names/counts and exceptions rather than printing OAuth tokens, full personal transcripts, document contents or the provider's private prompt. A private HTTP 401 in an ordinary browser is expected; only valid Telegram-signed calls should succeed. A 401 from IBM or Google is a separate provider-auth issue. Verify current time/system clock when investigating provider token expiry. No provider request should be silently treated as a successful Calendar write.

## Handover limits and next verification

The sanitized GitHub repository intentionally excludes the original personal database, Google/IBM/Telegram/ngrok secrets, Microsoft SSO sessions, course downloads, encrypted tokens, private backups and user conversations. It is an **independent code checkpoint**. The documentation explains how developers obtain their own external-service access; it cannot supply access they have not been granted by the relevant owner/institution.

Outstanding items at handover: **safe public conversation/Calendar audience**, unresolved **“2 a week”** Orchestrate action failure, third-party Google OAuth consent acceptance, independent Brightspace student SSO integration, evidence-grounded AI quiz generation, weaknesses/adaptation, meaningful academic monitoring/replanning, measured time savings, operator ownership and final deck/video/submission checks. The static homepage/privacy Telegram navigation improvements were included in the GitHub snapshot but not deployed to the original host when this documentation was first prepared.

No additional Calendar fix, Brightspace public enablement, live Google OAuth acceptance, automatic monitoring, public bot transfer or service restart is performed by editing these documents. The team must implement and demonstrate each item, update the status and obtain the original deployment operator's approval for changes to the live public service. See `SECURITY_AUDIT.md` for publication checks and the source code/tests for precise contracts. Keep the repository private until the team decides otherwise and confirms secrets/history are clean.
