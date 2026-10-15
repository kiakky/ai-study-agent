# Sanitized publication audit — 15 October 2026

Scope: the independent publication repository, based on deployed source `5c28756`,
retaining earlier setup/read-only safeguards and adding undeployed navigation links.
Original runtime repository/history, services, data and credential files are not exported.

## Findings and exclusion controls

- Source exported only from allowlisted tracked paths. All 109 proposed files are UTF-8
  source, tests, documentation or disabled/placeholder examples. No live student records,
  OAuth clients/tokens, environment values, Microsoft SSO/cookies, course downloads,
  backup files, logs, virtualenv or original private Git history are included.
- Reviewed manifest and staged changes; tracked working files AND exact index blobs scanned.
  Every reachable commit tree and commit message/author metadata in this independent repo
  is scanned by `scripts/audit_publication.py --history`, including the two retained prior
  sanitized roots. Private path, binary/symlink, token/key/JWT, personal machine/tunnel and
  unexpected email checks have no unresolved findings. Only the explicitly approved public
  support email and synthetic/example addresses are accepted.
- Supplemental Python literal entropy review found one candidate: a public Runtime model
  identifier in `watsonx.py`, not a credential. Values of findings are not dumped by audit.
  Verified 16 representative credential/session/database/config/backup paths are ignored.
- Placeholder `.env.example` and disabled JSON templates require teammates' own accounts.
  Public Telegram URL was verified from the existing bot's public identity; no token is
  published. Approved support email remains in the privacy policy. No other personal
  contact information is intentionally published.
- Positive Brightspace read-only allowlist/metadata checks, own-origin requirement, isolated
  session cwd, pinned package and environment filtering are preserved; WXO fields are
  explicitly stripped. No original developer school account is supplied or shared.
- Pattern/entropy scans are not a mathematical guarantee of absence of all possible secrets.
  Independent source-only export, manual manifest review and no private-history transfer
  are the primary boundaries. Audit future commits before publishing, too.

## Verification for this checkpoint

- **96 distinct focused offline tests passed**: 16 API/privacy/security, 53 Orchestrate
  transport/provider/Calendar, 23 LMS retrieval, four publication safety tests.
  Initial focused run found one provider test lacking the publication's required synthetic
  school origin; fixed the test fixture with its own temporary session directory and reran
  the affected 13-test module successfully. No production guard was relaxed.
- Separate runtime-source navigation worktree: four public-page/protected-API tests passed.
- `pip check` passed. Python source parsing, whitespace and relative documentation links
  checked. Clean-machine dependency installation was not performed; pins are direct, not
  a complete transitive lock. Existing LMS tests emitted ResourceWarning messages about
  unclosed SQLite connections; they passed, but test cleanup remains a maintenance issue.
- The earlier **282 full regressions** are historical Calendar checkpoint evidence, not a
  full rerun here. Known real clarification bug remains open. Fresh-user Google OAuth,
  public Telegram onboarding and complete Calendar phone acceptance are NOT verified.
- No live IBM inference, Google Calendar writes, Brightspace operations, Telegram test
  messages, production DB reads/changes, OAuth changes or service restarts in these tests.

## Publication and live-host boundaries

Publication is authorized by the project owner, to private `kiakky/ai-study-agent` only.
Authenticated connector confirmed private visibility and an empty remote before upload.
Git CLI credentials were unavailable; the authenticated GitHub connector is used instead.
Its new remote history contains a README bootstrap and the complete audited tree, not
private runtime history. Final branch/head/tree verification is recorded in the task's
publication receipt and completion report; do not infer success from this document alone.
The local sanitized checkpoint and connector-created remote commit can have different
commit hashes/parents while sharing the exact same Git tree. Clone the remote normally.

The live host remains at `5c28756`. New navigation is included in the repository and a
separate local source branch only; deploying it requires a separate controlled deployment.
No live databases, credentials, backups or running integrations were changed by handover.
