# Two-week scheduling and silent Telegram reply repair (deployment pending)

2026-10-15. Isolated orchestrate-scheduling-repair worktree, based on production 8d0b38a.
Production services/source/private configuration/data remain unchanged. No real Calendar
writes, Telegram messages, Brightspace access, OAuth changes or GitHub operations.

## Evidence from the reported real requests

Narrow read-only inspection used only recent conversation status/delivery metadata,
structured tool-result shapes and static validator messages. No user/assistant text,
account identifiers, Google event details, tokens or raw provider responses were printed.
Both scheduling requests reached Python; neither has a stuck lease.

Account B's greeting completed but its Telegram delivery remained unconfirmed. Its
scheduling request persisted two availability actions (one rejected as past, one successful)
and two rejected draft actions. Availability succeeded for three slots, but its end boundary
was 23:59:59, producing a span one second short of 14 whole days. The drafts failed exact
local-day boundary and expected-weekday validation. The agent continued consuming its fixed
budget and ultimately raised tool_limit. This was recovery cycling through different invalid
actions, not evidence of an identical-action loop. No proposal/write was confirmed.

Account A's scheduling request reached the router and automatic preference/connection
reads, then failed strict structured-action validation before availability executed. The
exact rejected operation/arguments were not retained and cannot be reconstructed safely.
Its error reply failed Telegram delivery. Logs contain both ordinary-reply and error-reply
delivery failures; the old handler only logged these and made no delivery retry. No OAuth
failure or stuck per-user lease was observed. External connectivity failures are distinct
from model scheduling failures; /start working does not prove every send succeeds.

## Small fix, unchanged execution safeguards

- Add a validated next_two_weeks route, resolved in Python as the next two full calendar
  weeks, Monday midnight to Monday midnight. Exact displayed dates remain reviewable.
- Extract only explicit total/weekly session count from the current conversation. If absent,
  ask how many sessions per week; saved duration/windows/timezone are not a frequency.
- Python checks each week separately under the authenticated user's Calendar/defaults,
  combines checked slot references, and validates one complete proposal's weekly counts.
  The model no longer calculates date boundaries or orchestrates repeated weekly searches
  for this case. Temporary overrides and normal one-week/custom paths remain available.
- Stop repeated identical model actions before a duplicate read; return a useful validated
  planning error/question immediately instead of spending the remaining budget on invalid
  drafts. Keep existing five-turn/twelve-operation limits; no approval/write tools added.
- Retry Telegram delivery only, at most three attempts, for transient NetworkError/short
  RetryAfter. Never rerun inference, proposals or Calendar execution to retry a message.
  Proposal-review delivery uses the same helper. Cached replies remain available.
- Bound queued conversational work to 120 seconds. A timed-out request gets a clear error;
  thread cancellation flags and existing SQLite lease/generation fences prevent late work
  from presenting results or reusing the timed-out message. Other users remain independent.
- Add payload-free action/result diagnostics and coarse structured-validation diagnostics.

Identity, OAuth, saved settings, exact review presentation, approvals/expiry/idempotency,
conflicts/ownership/ETags, move/cancel executors and the two-tester gate are retained.
No schema migration, credentials change or new framework is required.

## Verification

Targeted tests reproduce both reported request strings; frequency clarification and follow-up;
canonical boundaries and balanced weekly slots; simultaneous users with different defaults;
custom-range validation; repeated/malformed actions; missing subject; action-limit delivery;
greeting/error delivery retries without model replay; persistent network failure; timeout
error delivery, release of leases and rejection of late worker results/Calendar actions.

One live synthetic scenario passed with four inference submissions: route/clarification,
route for the natural weekly-frequency follow-up, one find_study_periods action, one
propose_plan action. The result contained six checked sessions, three per week, with
mocked Google execution, temporary SQLite and synthetic users. No further live calls needed.

Final full regression: **282 tests passed**, Python exit code **0**. This includes
13 new targeted regression cases. Existing pending-amendment recovery tests also pass.
Syntax and git whitespace checks passed. Logs are retained privately under .local-backups.

## Limits and deployment

A prolonged Telegram outage cannot guarantee delivery: attempts are bounded and completed
responses remain cached; no infinite retry or Calendar/model replay occurs. A lost Telegram
acknowledgement can cause a duplicate text message, never a repeated Calendar operation.
The model can still fail strict schemas; it fails closed and now logs only safe diagnostic
categories. Custom ranges rejected by existing validators return an actionable error rather
than being silently expanded or endlessly repaired. Existing seven-per-week validation is
retained for balanced two-week plans; no frequency is silently invented.

Deploy only after user approval: inspect current processes/Git; stop only the identified bot
when ready; non-consuming Telegram queue checks with no offset/discard; stop API, preserve
ngrok; fresh private supported SQLite backup/integrity; fast-forward the approved repair;
start exactly one API, verify local/public health/security; recheck queue and start one bot;
verify menu/polling, SQLite preservation and no unexpected Calendar activity. No migration.
Do not retry the old real messages automatically. After deployment, resend the full two-week
request, answer the frequency question naturally, review all proposed dates, amend one session,
and test approval only when ready to authorize the exact shown events. Repeat on both accounts.
OAuth Production, public onboarding and GitHub publication stay paused until acceptance.
