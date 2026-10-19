# Owner quiz failure: approved narrow diagnostic

19 October 2026. Deployed code `4aa351a`; investigation isolated on `owner-quiz-retrieval`. No fixes deployed or production source changes made.

## Confirmed observations

- Inspected only relevant owner failed-turn tool-result structure and source metadata through a SQLite read-only connection with `query_only=ON`. No student text, source contents, titles, source IDs, account IDs or credentials were displayed or exported.
- Three failed turns contained document-reading failures. Course and material catalog responses were successful and paginated. Successful reads within those turns returned 743 characters, and one later read returned 2,000 characters with a continuation available. No saved quiz receipt was present.
- Matched one failed read to an indexed document using the existing request digest and approved source metadata. The document had a valid topic identifier and no cached extraction.
- Reproduced that read through the existing owner connector. It returned an MCP tool error. Classification of its private error text into fixed categories identified a size-limit refusal, with no authentication, missing-resource or network indicator. The actual error text was neither printed nor persisted.
- The diagnostic used a nonpersisting extraction sink and a read-only production database connection. Normal upstream-managed authentication/session startup remains the existing connector behavior; this diagnostic did not inspect or manually modify sessions.
- Reviewed only installed package code and schema, not credentials. Version 3.13.18 has a 10 MiB inline delivery cap and a separate 50 MiB buffered-download cap. Its tool schema provides no size/chunk argument. Disk mode requires `downloadPath`, which the application deliberately does not expose. The diagnostic did not enable disk mode, change these caps or modify the package. The exact document size was not established.

## LLM/tool configuration check

A synthetic probe of the fully composed owner prompt and schemas confirmed ten unique advertised tools, including `brightspace_read` and `prepare_practice`. The obsolete “No quiz-generation tool exists yet” instruction was absent. This rules out those particular configuration omissions; it does not establish that model navigation is reliable for a large course catalog.

The native loop currently permits eight model rounds. Catalog paging, unavailable documents and successful partial reads can consume those rounds before quiz preparation. The runtime generic `brightspace_unavailable` response hides why a particular document is unreadable and suggests retrying or selecting another source. This is an information gap for model decisions, compounded by a test fixture with only two small, readable documents.

The size refusal proves a cause for one affected read. It does not independently explain every failed read or both original generic conversation-level failures. Those earlier broad exceptions lack enough retained diagnostic detail to assign a more specific cause honestly.

## Safe next implementation proposal

1. Recognize the pinned MCP server's known document-size refusal as a sanitized typed failure. Retain unknown failures as unknown; do not copy raw messages into logs or model context.
2. Give the LLM source-specific availability feedback so it can select alternative relevant evidence or explain a genuine limitation. Do not repeatedly attempt an unchanged, size-blocked source in the same turn.
3. Test a realistic paginated course catalog with mixed readable, oversized and partial documents, informal W3 requests and existing conversation history. Verify that available relevant evidence reaches Mini App preparation; missing evidence must produce an honest clarification rather than invented content.
4. Preserve nonowner behavior and current filesystem/authorization boundaries. Do not expose a disk-download option, modify upstream packages or globally raise inference/tool limits as a shortcut.

Supporting offline checks: 13 native LMS tests and 12 essential LMS tests passed. These checks cover mocked reads and safety contracts; they do not establish a live owner fix. No real Telegram messages, Calendar actions, database mutations or deployment were performed by the diagnostic.
