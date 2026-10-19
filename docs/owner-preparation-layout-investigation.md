# Owner preparation failure: isolated investigation

Base: deployed `c0c8235`. Candidate changes remain uncommitted in the
`owner-preparation-failure` worktree. Nothing from this investigation is deployed.

## Approved live inspection

The user explicitly approved diagnostic metadata inspection for the two failed
owner turns. The diagnostic uses a read-only SQLite connection and query-only
transaction. It reports tool statuses, offsets, lengths and numeric formatting
statistics only. It does not report message/source text, identifiers or secrets,
change records, or invoke external services. Formatting statistics are computed
privately; only counts leave the diagnostic.

The first turn listed courses and 46 material entries, read two 6,000-character
chunks, then returned `practice_unavailable`. An initial material call had returned
`brightspace_unavailable`, but subsequent listing and reading succeeded. The second
turn also listed and read material, then ended without calling preparation.

The first 6,000-character excerpt contained only 108 non-whitespace characters:
98.2% was whitespace, including 2,670 leading blank characters. Its first 2,000
characters contained no readable text. The next chunk contained 329 non-whitespace
characters. The second turn read the same-shaped first chunk and a separate
743-character excerpt containing 633 non-whitespace characters.

Original preparation arguments, selection counts and the underlying exception
were not persisted. Therefore this inspection cannot prove which ranges the
model selected, what caused that historical generic exception, or the relevance
of either document. A successful read is not proof of sufficient learning content.

## Reproduced defect and smallest candidate fix

PDF extraction preserves page layout. The reader and preparation code previously
used raw character positions, allowing the model to select a substantial-looking
range that is entirely blank. Preparation rejects fewer than 30 readable
characters, and previously converted that local error into the generic
`practice_unavailable` result. This is a reproduced failure mechanism consistent
with the numeric metadata, not a confirmed explanation of every live failure.

The candidate gives Brightspace reading and preparation the same readable text
view: remove leading/trailing blank padding, collapse blank-line runs and remove
common margins. Preserve wording, relative indentation and internal spacing.
Original extracted segments and source revisions remain unchanged. Tool feedback
explicitly describes the readable-view coordinates. Uploaded-note coordinates
are unchanged.

Insufficient selected text now returns a specific recoverable result directing
the model to reread/select a readable range. It does not treat a bad excerpt as
proof that the entire document is missing. Constant preparation-stage/error-class
logging adds future diagnostic value without exception text or identifiers.
Authorization, citations, generation validation and approval boundaries remain.

## Verification

- Focused learning-text/LMS/quiz-refinement run: 46 passed.
- Focused learning-text/practice run after adding safe diagnostic logging:
  53 passed. This overlaps the previous run; counts are not additive.
- Native completion regression run: 8 passed.
- Syntax checks and `git diff --check` passed. The full suite was not repeated.
- Real IBM synthetic experiment uses a generated PDF with actual layout padding,
  actual extraction, fake external integrations and a temporary database. First
  trial failed with provider HTTP 500 before any tool execution. A separate trial
  passed in 16.52 seconds: saved a three-question Mini App quiz and passed the
  harness's grounding, resume, submission, scoring and isolation assertions.

Ignored artifacts: `tests/artifacts/preparation-layout-focused.log`,
`preparation-storage-focused.log`, `preparation-loop-regression.log`,
`quiz-refinement-owner-15d534b442.json` (provider failure), and
`quiz-refinement-owner-a4d14b326e.json` (successful synthetic trial).

## Remaining uncertainty

The candidate has not been tested against real owner learning material and has
not been deployed. It fixes one demonstrated retrieval/preparation mismatch;
it does not establish reliable live discovery or scheduled Brightspace quizzes.
Any release requires explicit approval followed by an owner live test. Root
README.md/HANDOVER.md edits and all preserved worktrees remain untouched.
