# Proof-of-concept startup: upstream-managed authentication

This startup-only release supersedes the strict/middle-ground launcher as requested.
SESSION_EXPIRED indicated a normal expired token, not a missing session. The active
application now lets upstream handle that lifecycle instead of requiring a manual
operator refresh before each successful MCP connection.

The launcher directly runs the installed `brightspace-mcp-server@3.13.18` entrypoint,
with update checks/self-update disabled. No npx/@latest lookup or dependency installation
occurs. Local non-secret settings supply the tenant, username and session ROOT; upstream
resolves its account directory. The child cwd is the installed package, avoiding the
project's .env, and only Windows/browser runtime environment variables are inherited.
Provider/bot/Google secrets and arbitrary parent D2L tokens are excluded.

Accepted upstream behavior: global Brightspace config/native credential storage may be
read; authentication state may be migrated, tokens refreshed/persisted, and a visible
school login/MFA requested when needed. These effects can occur during actual startup
or a read, so the live checker no longer claims zero maintenance. No such operation was
executed during automated testing or deployment.

The stock catalog exposes its normal tools. Existing application-side read-tool
allowlisting, disabled document-download default and owner-bound source access remain
unchanged. The nine-tool custom server bootstrap is no longer active. The stricter
operator command remains an optional historical diagnostic; its expiry result does not
predict whether upstream can automatically refresh the session.

Run the normal compatibility check from the project root:

```powershell
python scripts/check_brightspace_startup.py --live --read-courses
```

It starts one temporary upstream MCP child, performs one read, prints no course contents
and closes the child. Allow time for any visible school login/MFA. Success verifies that
read only, not the unfinished retrieval adapters or Brightspace LLM integration.
No API, Telegram poller or ngrok is required for this local check.

Validation: 33 synthetic LMS/startup tests and 13 synthetic provider tests passed.
These include fixed-package rejection, source cwd/environment isolation, raw session-root
handoff and the existing owner/read-tool boundaries. No real credentials, sessions,
SQLite data or external messages/events were accessed or changed during the rollout.
Other adapter fixes and LLM work remain sandboxed.
