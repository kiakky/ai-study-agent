# Calendar clarification repair — 18 October 2026

Status: implemented on the isolated calendar-clarification-repair branch, based on private/local main at 5c28756. NOT deployed or pushed. Real IBM reasoning passed with synthetic data and mocked Google. Real Telegram/Google acceptance remains after approved deployment.

## Confirmed findings

The historical failed Telegram payload was not inspected or recovered. Live synthetic testing reproduced a concrete failure with the same conversation: IBM emitted temporary_preferences: null for find_study_periods, whereas the local schema required an object. Validation rejected the request before planning could use saved defaults.

Additional weaknesses:
- Orchestrate validated arguments before the normalization already used by native Calendar tools, so stringified numbers could fail prematurely.
- Frequency answers were routed through the model again, allowing repeated clarification or inconsistent routing.
- An early staged implementation accepted alternate model clarification wording, which its own continuation matcher did not recognize. Live testing caught this and the final implementation always emits Python's canonical frequency question.

## Repair

Changed production modules:
- backend/services/calendar_tool_inputs.py: recognize narrow whole-message replies immediately following Python's own frequency question; ask a specific two-or-three choice for a range; normalize null optional array/object containers to their empty defaults while preserving required-field validation.
- backend/services/calendar_agent.py: resume those actor-scoped clarification answers without reclassification; keep the frequency prompt canonical.
- backend/services/orchestrate_actions.py: use the existing schema-aware normalization before strict validation; report only known operation/schema keyword diagnostics without payloads.

Tests/harness:
- tests/test_orchestrate_calendar.py: persisted multi-turn dialogue with service recreation, alternate model clarification wording, stringified counts, null temporary preferences, saved-default preservation, two-user isolation, cleared history, unrelated/mixed-approval text and schema denials.
- scripts/check_calendar_clarification.py: opt-in bounded live IBM test (at most ten inference attempts per invocation), temporary SQLite, synthetic actors and mocked Google. No configuration access without --verify. It never sends Telegram messages or real Google operations.

Unknown/identity fields, required nulls, invalid counts, approvals, ownership, conflicts and idempotency protections remain enforced. No migration or private configuration change is needed. The two-tester gate is unchanged.

## Verification

- Final real IBM dialogue scenario passed in five model submissions: initial frequency question; reported range and exact-frequency replies; four Machine Learning sessions with two per week; selected first-session amendment by one hour; preservation of the other three; invalidation of the previous review; explicit rejection with no writes.
- The isolated live amendment scenario also passed. An earlier amendment assertion failure was a test-harness UTC/local formatting error, corrected by formatting the request in the saved Asia/Singapore timezone.
- Live variability exposed the null-override schema failure; its exact field and type were diagnosed without printing model payloads or private data, and a regression was added.
- Previous full run: 290 tests passed in the escalated Windows execution context. The earlier restricted-runtime DPAPI error was environmental; the synthetic DPAPI test passed with normal Windows access. The initial source staging copy's omitted SVG was also corrected.
- Final complete offline suite: 291 tests passed, zero failures/errors/skips, exit code 0. This includes 35 Calendar adapter tests and Windows DPAPI synthetic-token coverage. The newly added null-override test initially omitted its saved study window; that synthetic fixture was corrected before this clean full run.
- Syntax and whitespace checks passed; the updated four-file patch passed git apply --check against the original root.
- Harness dry run passed; it accessed no configuration or external service.
- No production DB access, real Calendar writes, Telegram messages, Brightspace operations or deployment during this work.

## Environment relationship and safeguards

- Original development root: main at 5c28756, with pre-existing README.md and HANDOVER.md modifications preserved.
- Eleven pre-existing registered worktrees were clean before repair. The prior scheduling fix 2414915 is an ancestor of 5c28756.
- Separate sanitized publication checkout: .local-backups/publication/ai-study-agent, handover-2026-10-15 at 548a96f, with local main d25c173. Its current GitHub remote head was not queried. Do not push private development history into that repository.
- Read AGENTS.md / Agents.md (same Windows file), README, HANDOVER and all three design/provider notes under docs.
- Initial restricted-runtime process inspection was denied and network checks returned EACCES. User authorized testing; escalated execution allowed IBM and Windows DPAPI testing. Escalated process inspection listed no Python/ngrok services at the inspection time. Local API health returned ECONNREFUSED earlier. A running deployment revision was not established; 5c28756 is the documented deployment source and verified root checkout.
- A source-only staging copy was used initially because restricted execution could not write Git metadata. After escalation, a proper isolated Git worktree was created. Neither original root source nor publication checkout was replaced or merged.

## Deployment approval and phone acceptance

Deployment requires explicit user approval. Before deploying, inspect the actual service/tunnel state and current source, perform non-consuming Telegram queue checks through existing application code, and make a separately authorized consistent private SQLite backup with integrity verification. Preserve ngrok, Google OAuth configuration, private credentials and existing student data. Start at most one Telegram poller; no automatic replay/discard of pending updates. No schema migration is needed.

After approved deployment, test in Telegram:
1. Ask for Machine Learning study sessions for the next two weeks.
2. Reply "2-3 for each week should be enough", then "2 a week should be enough".
3. Expect a specific two-or-three choice, then four exact proposed sessions, two per week.
4. Amend one session and verify the other three remain unchanged.
5. Reject once and verify no Google write. Approve only the exact displayed plan when deliberately ready, then inspect the correct Google Calendar.
6. Verify individually approved move/cancel flows and isolation between the existing testers.

Milestone 1 is ready for deployment review, but is not yet real-Telegram accepted. Milestones 2–5 and public-audience enablement have not started.
