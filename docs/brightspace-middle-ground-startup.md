# Brightspace startup: explicit operator maintenance

Historical alternative, superseded by [upstream-managed startup](brightspace-upstream-startup.md).
The active application no longer requires this command's strict status/refresh workflow.

Startup-only successor to `4e8c236`. Retrieval adapters, source storage, database schema,
Calendar, Telegram, quizzes and LLM integration are unchanged. The unrelated adapter
branch remains sandboxed. This release does not enable Brightspace conversations.

## What changed and why

The earlier operator check was pointed at the session root. Reviewed upstream 3.13.18
uses `root/accounts/sha256(JSON.stringify([tenantOrigin, configuredUsername]))/` when a
username is configured. Path-existence checks confirmed that the expected account's
directory/session file exists and the root-level session file does not. No session
contents were inspected to establish this. The launcher now mirrors that deterministic
layout, accepts an already explicit session directory, and does not enumerate other
accounts or load global authentication configuration. Directory existence alone is not
proof of valid authentication, ownership or expiry; runtime still validates those.

Five non-secret operator settings may be persisted in ignored
`config/brightspace_runtime.json`, with explicit process environment overrides. It
accepts only the five known string fields and never loads dotenv on the child's behalf.
The session-root setting remains a root; account resolution runs before child creation.
This removes dependence on a particular PowerShell window's environment. The local
metadata file contains no token, cookie, password, key or browser-state data.

Normal MCP startup still uses the pinned, source-verified 3.13.18 installation, isolated
environment, nine reviewed registrations, same-tenant GET-only transport and existing
modern owner session/native key. It cannot migrate, refresh, open a browser, read a
stored password, or invoke the upstream config/update/auth CLI. Owner UPN comparison is
case-insensitive, matching upstream's account comparison; tenant equality stays strict.
Account-directory hashing preserves configured username casing exactly as upstream does.

Maintenance is a separate executable entrypoint, never imported by normal MCP startup.

| Command | Effect |
|---|---|
| `python scripts/brightspace_operator.py status` | Reads existing session/native key and reports compatibility or a fixed failure category. No HTTP or state changes. |
| `python scripts/brightspace_operator.py refresh` | Validates existing owner/tenant, makes one explicit token exchange, then conditionally saves through upstream encryption. No browser, retries, deletion or login fallback. |
| `python scripts/brightspace_operator.py login` | Opens the school browser for manual login/MFA. Verifies the returned owner/tenant before saving the token and staged browser state. |

No school password is accepted in Telegram, command arguments or the operator terminal.
The browser uses the school's actual sign-in pages. No global stored password/TOTP/config
loader is called. Explicit login can initialize secure storage and migrate browser state
within the selected account directory through upstream library behavior. It does not
scan or migrate the root/other accounts, invoke the updater, retire legacy profiles or
delete an old session. Wrong/missing identity rejects the returned token before saves.
Browser state is staged until that owner check; failed login does not opportunistically
save new SSO state. A save failure after successful token persistence can leave a valid
token with older browser state; run status rather than blindly repeating login.

Refresh accepts expired token material only in the operator path. Normal reads still
reject expiry. Missing material, bad identity/tenant, mint failure or concurrent state
replacement stops without clearing old authentication. Refresh is opt-in per invocation,
not a background scheduler; long demos may need another explicit refresh or login.

## Operator acceptance

Use an ordinary PowerShell window as the Windows owner of the session, from the live
project root. No five-variable block is required once local settings are supplied.

```powershell
python scripts/brightspace_operator.py status
```

If SESSION_EXPIRED appears, deliberately run `python scripts/brightspace_operator.py refresh`.
If login is required, deliberately run `python scripts/brightspace_operator.py login`
and complete sign-in/MFA in the browser. Do not guess account fields, delete session
files or run an unpinned setup command. BROWSER_NOT_INSTALLED means the package's
Playwright browser is missing; the operator command never downloads it automatically.

After status succeeds:

```powershell
python scripts/check_brightspace_startup.py --live
python scripts/check_brightspace_startup.py --live --read-courses
```

The first lists MCP tool metadata without a school request. The second makes one course
read and reports only success/failure. No Telegram bot, API server or ngrok is required
for these local operator checks. Live school authentication remains unverified until
the operator completes these steps; deploying code is not proof of a successful login.

## Verification and scope

Synthetic tests cover path resolution without session reads, private settings precedence
and rejection of unknown fields, expiry versus explicit refresh, wrong owner/tenant,
failed/invalid mint preservation, conditional saves, staged browser state, manual browser
flow, CommonJS/ESM browser exports and all earlier startup/read-only contracts. Actual
public Playwright metadata confirmed that the installed browser binary exists; no browser
was launched. No real session/key was consumed, authentication state changed, school/API
request made, or login/refresh executed as part of automated testing.

Final validation: 325 full regression tests passed in 174.388 seconds; the focused
LMS/startup suite passed 31 tests in 9.664 seconds. A final standalone Node run passed
24 cases, none skipped, including the CommonJS/ESM correction. The Node suite is also
invoked by one Python test and should not be counted as additional Python tests.
The live deployment receipt is recorded separately. No database
initialization, production migration or backup is necessary for these source/config-only
changes; production SQLite is not opened by deployment or operator commands.
