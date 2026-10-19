# Milestone 1: immediate reviews and conflict checks

The real October 18 Telegram test demonstrated five study sessions, a selected-session
amendment, exact human approval and visible Google Calendar events. It also exposed an
unnecessary intermediate permission loop: the model described checked slots but asked
whether to prepare the actual review. The earlier conversational approval was therefore
not an approval of a backend-presented frozen proposal.

Native instructions now explicitly require preparing that review in the same turn once
a currently requested scheduling task has sufficient details and suitable slots. A prose
timetable is an intermediate step. The reported `Approved. Go ahead and set those 5 events
in my calendar` wording prepares a missing review; it does not bypass exact human approval.
Availability-only and hypothetical questions remain informational. No phrase classifier,
new execution tool or automatic write approval was added. The model is also instructed to
keep tool names/references and Markdown markers out of ordinary replies.

The independent review footer now says to use the review controls to approve, reject or
check current status. It no longer claims nothing has been written, which became false
when the preceding approval-button message was edited to show successful creation.
The actual result message remains generated from the saved execution status.

A wider live check exposed an intermittent clarification continuation that stopped too
early or selected only one week's sessions. The instructions now explicitly connect short
workload answers to the existing request and require checking total/per-week distribution.
Proposal results expose the factual cumulative session count with completion guidance,
without interpreting natural language in Python. The subsequent original-dialogue check
passed four sessions and the selected amendment/rejection (seven submissions). This is
an exercised recovery, not a guarantee that all model output is deterministic.

## Verification

- Live IBM synthetic acceptance: direct five-session request, the reported approval after
  a prior prose timetable, and a five-session request with busy Calendar periods. All
  three prepared an exact proposal in the same turn, distributed two plus three across
  weeks, preserved explicit human approval and passed duplicate protection (13 submissions).
- The conflict scenario excluded a completely busy Monday and avoided occupied parts of
  Wednesday and the following Monday. All proposed intervals were checked against the
  synthetic busy periods; Google writes were mocked.
- Added offline coverage checks a fully busy day, partial-day occupancy, fragmentation
  too short for the duration, and a conflict appearing after review but before approval.
  That last race blocks the old approval without writing; a changed timetable needs a
  new review, rather than silently executing a different schedule.
- The complete offline suite passed 317 tests. The native suite passed 26 tests and is
  rerun after the final completion-guidance change. Telegram handler
  coverage asserts the actual delivered footer has no stale no-write claim.
- Live greeting/reset/topic-change checks and original dialogue/amendment checks are run
  separately to guard against the review instruction reviving old tasks.

All automated fixtures use temporary SQLite and synthetic actors. Google/Telegram remain
mocked; no production records or real Calendar contents are inspected during testing.
No schema or configuration changes are required. The existing public audience stays at
two testers. Milestone 2 is untouched.

## Operator acceptance

Create a personal busy Calendar event inside preferred study hours, then ask for sessions
across that period. The proposed sessions should avoid that event and retain the requested
duration/count when enough free time exists. A fully occupied day should be skipped. If
there is insufficient availability, the agent should explain or ask about alternatives,
not silently discard sessions or violate constraints. Adding a new clash after the exact
review should block the outdated approval; request a revised plan and approve it afresh.

Review exact dates/times, amend one session, then approve using the displayed review.
Confirm actual events and final status. Natural-language reliability still requires this
real Telegram acceptance; synthetic model checks are evidence for the exercised cases.
