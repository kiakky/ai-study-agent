# Bounded private Brightspace reading release

19 October 2026. Scope: owner-only oversized course-document retrieval. No UI, quiz-generation prompt, Calendar, database-schema or audience change.

## Behavior

Small documents continue through inline reading. A known 3.13.18 inline-size refusal triggers one internal temporary read of the same authorized course/topic. Unknown, authentication or network errors do not trigger disk retries. The application picks a random private job directory under `<project>/.private-lms/temporary-downloads/` and a fixed extensionless filename. PDF, DOCX and PPTX are supported by this fallback.

The raw file ceiling is 20 MiB, enforced while bytes arrive, independently of existing bounded text reads/model inputs. Larger files return a specific nonretryable size-limit result. The model can select other relevant evidence or request a smaller excerpt. It cannot specify a download path, filename, URL, different actor, submission or attachment identity.

Python reads only its predetermined regular file after size/signature, hard-link, reparse-point and file-identity checks. It removes only that job directory after success or ordinary failure. Windows ACLs grant the current OS identity and SYSTEM; POSIX directories use 0700. No stale-directory sweep is performed. A hard crash can leave a private job behind; manual inspection/cleanup is not automated in this release.

Only authorized extracted source text and provenance are persisted for the temporary fallback. The downloaded binary is not copied into the persistent original-file cache. Existing small-file behavior and existing original caches are unchanged.

## Startup and policy boundary

The existing pinned upstream `build/index.js` remains the entrypoint and retains normal upstream-managed authentication. An application-owned Node `--import` preload bounds `D2LApiClient.getRaw` response bodies. It does not load sessions itself, change account resolution or edit the installed package. Its package version and four reviewed source hashes must match before imports; an unreviewed package fails closed before its entrypoint runs.

`ReadOnlySession.call_tool` and its exposed schemas still reject filesystem arguments. A separate internal capability accepts only an application-created scratch object, validates the same positive course/topic identifiers and original schema, and supplies the fixed directory/name. The capability is enabled only by the existing application connection using the bounded startup. Retrieval checks actor-owned sources before entering that path.

## Verification evidence

- Eight synthetic actual-upstream-handler cases passed with the production preload: 12 MiB PDF/DOCX/PPTX extraction, original inline refusal, and 21 MiB refusals with accurate, missing and understated headers. Installed source hash unchanged.
- Four actual MCP stdio cases passed through the production policy/adapter/retrieval/source-store path with a synthetic API: large PDF/DOCX/PPTX text cached without binary retention; an oversized PDF refused. All cases checked cross-actor denial, public-path denial and empty scratch jobs afterward.
- Nine JavaScript boundary tests passed for streaming ceilings, cancellation, errors and receiver preservation.
- Focused Python tests cover scratch ACLs/junctions/cleanup, exact internal capability constraints, outside returned-path denial, unknown-error behavior, typed size-limit feedback and rejection of unreviewed preload code.
- Existing owner MCP startup compatibility passed with the actual configured installation and new preload. This initialized/listed tools only; it did not read courses/documents or prove live owner quiz completion.
- Final offline regression: **458 tests in 253.162 seconds; 457 passed and one platform-specific test skipped**. Results are in Git-ignored `tests/artifacts/lms-private-release-suite.log`. Python compilation, Node syntax and whitespace checks passed.

The old tiny stdio fixture intentionally has no upstream API implementation. Its test continues to exercise inline transport/policy without importing nonexistent modules; full preload/private-reading compatibility is exercised separately by the actual-package stdio harness above. No production path disables the preload.

Earlier feasibility findings are recorded in `brightspace-temporary-download-experiment.md`; its pre-integration statements describe that earlier phase.

## Live acceptance after deployment

Owner: request a short quiz from the actual course/week, then check Generated Practice. Retest an explanation request against a formerly oversized learning document. Nonowner: confirm the previously working uploaded-material quiz flow remains available and owner LMS access remains denied.

The actual failing school document's size was not measured. Files above 20 MiB, scanned-only PDFs and material without sufficient readable subject content remain limitations. Other model-navigation or provider failures are not claimed resolved solely by this file-reading change.
