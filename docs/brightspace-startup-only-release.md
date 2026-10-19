# Startup-only release

Historical checkpoint: superseded by [middle-ground startup](brightspace-middle-ground-startup.md).
The newer launcher resolves upstream account directories and loads ignored local
non-secret settings, so repeated per-terminal environment setup is no longer required.

Built directly from deployed `84b0c6d`. Only `server_parameters()` in the existing
Brightspace service changes; all other runtime additions implement controlled startup.
No retrieval adapters, response decoding, source storage, URL filtering, assessment
discovery, database schema, Calendar, bot, LLM tools or quiz changes are included.
The original mixed development branch remains isolated and is not merged.

Release validation: 323 full synthetic regression tests passed in 135.264 seconds;
the startup Node suite's 17 cases run within that suite. A final focused LMS/startup
run passed 29 tests in 10.322 seconds. The two pre-existing environment tests now
provide explicit synthetic settings and assert the stricter startup environment.
All non-startup application source is byte-identical to baseline `84b0c6d`.

Startup requires explicit Node/package/session/tenant/owner settings. It does not
discover or change private configuration. Missing settings fail closed; no fallback
to npx, stock startup, global configuration or interactive authentication is allowed.
The local runtime verifies version 3.13.18 and 119 upstream source hashes, reads only
an existing modern owner-bound session/native key, never migrates/refreshes/authenticates,
and permits only the nine reviewed registrations with inline-only filesystem argument
restrictions. These server-side restrictions are part of the controlled bootstrap;
the separate Python policy/adapter fixes are not included. Existing Python retrieval
and disabled-download defaults retain their deployed implementation.

## Operator compatibility verification

Run from the live project in PowerShell, as the Windows user who owns the session.
The checker does not load .env, touch SQLite, contact Telegram or start a bot.
Supply the five non-secret settings in this terminal session or use the named options:

```powershell
# Replace placeholders locally. Do not paste authentication material into chat.
$env:LMS_NODE_EXECUTABLE = (Get-Command node).Source
$env:LMS_MCP_PACKAGE_ROOT = '<absolute fixed package installation directory>'
$env:LMS_SESSION_DIR = '<absolute existing session directory>'
$env:LMS_TENANT_ORIGIN = 'https://<your Brightspace tenant>'
$env:LMS_EXPECTED_ACCOUNT = '<exact uniqueName of the authorized owner>'

.\.venv\Scripts\python.exe scripts/check_brightspace_startup.py
.\.venv\Scripts\python.exe scripts/check_brightspace_startup.py --live
.\.venv\Scripts\python.exe scripts/check_brightspace_startup.py --live --read-courses
```

1. Default: validates explicit setting presence/format only; no session/keyring access.
2. `--live`: verifies installed source hashes, reads the existing session/key, initializes
   a single temporary MCP child and lists exactly nine tool definitions. No school
   course request is made. The child closes on exit, including failures/timeouts.
3. `--live --read-courses`: additionally makes one authenticated read. Prints only
   success/failure, never course content, tokens, account metadata or native-key details.

Expected results are PASS lines and exit 0. Exit 2 means configuration is incomplete;
exit 1 means startup/read failed safely. Run no stock auth/setup/migration commands as
part of these checks. Missing/legacy/mismatched/expired state requires separately
planned operator maintenance, not a fallback that guesses an account or repairs files.
The checker does not install npm dependencies or initialize/bind production LMS tables.
Session authentication, tool discovery and a course read do not prove that the old
retrieval adapters or LLM integration are complete.

The package must be an existing trusted fixed installation with its dependencies,
not merely the downloaded public build source used by synthetic tests. Source hashes
do not lock transitive/native dependencies or substitute for a trusted installation.
The actual school/native-key compatibility is unverified until the live checks pass.
