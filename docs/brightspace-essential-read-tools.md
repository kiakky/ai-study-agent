# Milestone 2: essential Brightspace reading slice

Status: implemented in the isolated `brightspace-llm-essential` worktree; not deployed.
Base: `2e939aa`, the operator-verified pinned upstream startup. This work does not
change the startup launcher, authentication lifecycle, package version, production
configuration, database default initialization, public onboarding or service processes.

## Included

- The MCP connection now wraps the stock catalog in a read-only policy. Ordinary
  allowlisted reads require exact boolean read-only metadata. Only reviewed version
  3.13.18 permits mixed-mode `get_syllabus(courseId)` and
  `download_file(courseId, topicId)`, with disk/submission/identity arguments blocked.
  Forbidden legacy helper arguments are rejected before opening a subprocess.
- The adapter reads `contentTree`, preserving module/topic relationships, and binds
  inline document reads to the indexed course/topic pair. File descriptions never
  replace attachment contents.
- The response decoder accepts upstream metadata plus a separate extracted-text
  block. PDF/DOCX/PPTX text is treated as text, not original binary bytes. Returned
  paths are never opened. Unsupported visual/scanned formats fail visibly; truncated
  text is marked partial and its original file is not falsely claimed available.
- Small inline HTML/body/content/description fields are readable. Existing extraction
  handles their text and tables; no OCR or new storage service is introduced.
- The native conversation agent exposes three owner-only tools: `brightspace_courses`,
  `brightspace_materials`, and `brightspace_read`. The LLM chooses courses, sources,
  follow-up questions and answers. No natural-language keyword router classifies exams.
  Source text has citations, warnings, bounded continuation, and an untrusted-data marker.
  Raw MCP payloads, account bindings and private paths are excluded from the model view.
  All ordinary Telegram users retain their existing Calendar/conversation features.
- Per-result serialized limits and a 32,000-character cumulative source-result budget
  keep multilingual material within the existing native gateway context budget.

## Deliberately deferred

No quiz generation, announcement monitoring, broader exam discovery, new Calendar /
due-date horizons, upstream pagination correction, or advanced cache/revision redesign.
The existing backend operations remain available internally; only the three essential
reads are added to the LLM. Listing pagination only pages the already returned local
catalog/tree; it does not claim complete remote enumeration.

## Deployment prerequisites, not performed here

The existing optional LMS schema and an explicitly authorized owner connection binding
must exist before live use. No automatic migration/binding is introduced. Missing schema,
unbound/disconnected owners and read failures return sanitized errors without starting
a school connection. A later approved deployment must review the existing additive
schema/binding path without replacing student data. Never copy the private development
history/configuration into the separate sanitized publication checkout.

## Safe verification

From this worktree, using the project virtual environment:

```powershell
..\..\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_lms*.py"
..\..\.venv\Scripts\python.exe -m unittest discover -s tests
```

All new tests use temporary databases, synthetic school content, mocked Google/IBM and
synthetic actor IDs. The startup integration test runs a real Node child and Python MCP
stdio transport against a synthetic 26-tool peer in a temporary pinned-package layout.
It exercises the unchanged upstream launcher/environment, initialization/version,
discovery filtering, forbidden calls, course/content reads and inline extraction together.
It never imports the stock authentication entrypoint, loads a session or calls a school.

Scripted-model and mocked native IBM gateway tests demonstrate the full course-list ->
material-list -> document-read -> cited-answer tool protocol. They cannot prove the live
model's choices, semantic correctness or resistance to every prompt injection. Those
remain operator acceptance checks after an approved deployment. Documents remain
untrusted; no tool can approve/execute Calendar writes or perform LMS mutations.

## Validation result

- Full synthetic regression: **353 tests passed**, 238.289 seconds; Python exit 0.
- Final focused LMS/startup verification: **59 tests passed**, 44.932 seconds;
  Python exit 0. This final focused run includes the corrected real-Unicode fixture.
- Real Node/Python MCP stdio integration passed against the synthetic pinned package;
  the test is executed, not skipped, on this machine.
- Mocked native IBM gateway and actual Telegram handler/dispatcher tests passed.
- Python syntax and staged whitespace checks passed. Startup/configuration helper,
  operator live checker and database initialization match base `2e939aa` byte-for-byte
  after line-ending normalization.

The focused timing includes temporary SQLite/ACL work and is not a live latency
benchmark. No real model, school, Telegram or Google operation was tested. Production
SQLite, credentials, sessions, ngrok and the root README/HANDOVER edits were untouched.
The live checkout remains `2e939aa`; no service was restarted or duplicate poller started.
