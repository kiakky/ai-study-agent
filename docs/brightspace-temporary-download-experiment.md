# Oversized Brightspace documents: isolated feasibility experiment

Historical sandbox phase. Subsequent integration is documented in [the private-reading release](brightspace-private-reading-release.md).

19 October 2026. Worktree `owner-quiz-retrieval`, base `4aa351a`. Experimental helpers are not imported by the application or either launcher. No deployment, production database operation, real school request, authentication flow or Telegram/Calendar action was performed in this experiment.

## Conclusion

An oversized PDF can be read through a private temporary download without giving the LLM filesystem controls. The actual installed `brightspace-mcp-server@3.13.18` download handler rejected a synthetic 12 MiB PDF in inline mode and successfully streamed the same PDF into an isolated directory through the tested bounded wrapper. The existing Python extractor recovered the expected learning text. The installed package was not modified.

This proves the mechanism with the actual upstream tool implementation and a synthetic API. It does **not** establish live owner quiz acceptance or a finished production integration. Connecting this wrapper to the existing authenticated upstream startup remains a separate implementation step, subject to the user's decision on the inline-only boundary.

## Why inline-only was selected

The essential-read-tools design intentionally exposed only `download_file(courseId, topicId)`. It denied `downloadPath`, custom filenames, submissions and identity arguments. This avoided accepting arbitrary returned filesystem paths or retaining unexpected course binaries. Smaller files already arrived as extracted text, which was adequate for the early fixtures. See `docs/brightspace-essential-read-tools.md` and the existing `lms_policy.py` tests.

The proposed refinement preserves that model-facing contract. Python, after actor authorization, would own the scratch directory; a narrow internal bridge would own the fixed output filename. The LLM would continue selecting course sources and readable excerpts, never paths.

## Alternatives investigated

- The pinned tool does not advertise a size or chunk argument. Its inline cap is a fixed 10 MiB, with a separate 50 MiB buffered-download cap.
- Increasing the inline cap requires changing/forking the pinned upstream implementation. No package edit was made.
- Choosing smaller relevant course sources can help but cannot replace a required larger document.
- The existing disk mode already streams and validates files, but its native ceiling is 2 GiB. Simply enabling it and checking size afterward would permit too much disk usage. The experimental wrapper therefore limits the response body **before** it reaches the upstream disk writer.
- A separate school HTTP/authentication client would duplicate session and access-control responsibilities. This experiment instead reuses the registered upstream handler with an injected API wrapper. It does not replace authentication.

## Prototype design

Candidate raw-file ceiling: **20 MiB**, matching the application's existing document extraction/storage limit. This is deliberately separate from the existing bounded text sent to the LLM. Larger files are refused, not silently truncated into a purported complete document. The actual failed school file's size remains unmeasured, so this ceiling is not claimed sufficient for that file.

`bounded_download.mjs` wraps the handler's API client. It rejects declared oversized responses, counts actual bytes for missing or incorrect Content-Length headers, cancels oversized streams, and preserves other API methods and their receiver. The upstream disk handler retains its filename/type checks and partial-file cleanup.

`lms_temporary.py` selects `<project>/.private-lms/temporary-downloads/read-<random>/material.pdf`. There is no hardcoded Windows username, drive or teammate path. The root is Git-ignored and outside the frontend assets served by the API. Each concurrent operation gets a distinct job directory. Windows ACLs are replaced with grants only for the current OS identity and SYSTEM; POSIX mode is 0700. These are OS-account protections, not per-Telegram-user authorization; the existing owner-bound retrieval check must still precede any future use.

The PDF reader accepts only its predetermined file, rejects symlinks/reparse points and extra hard links, validates size/signature, and checks the opened file's identity. It never follows a returned MCP path. Success or ordinary failure removes only that job, including partial files; siblings are never swept. Hard process termination may leave a private job behind. Automatic stale-job deletion has intentionally not been implemented.

An initial Windows permission test caught explicit Administrators/OWNER RIGHTS grants surviving inheritance removal. The prototype was corrected to replace the scratch DACL, and the test then verified the actual resulting ACL. No system, user-account or installed-package permissions were changed.

## Tests and artifacts

The six actual-handler cases all passed:

| Synthetic PDF | Mode/header | Expected result |
| --- | --- | --- |
| 12 MiB plus PDF structure | Original inline | Refused by upstream |
| 12 MiB plus PDF structure | Bounded disk, accurate length | Downloaded, extracted, job removed |
| 12 MiB plus PDF structure | Bounded disk, missing length | Downloaded, extracted, job removed |
| 21 MiB plus PDF structure | Bounded disk, accurate length | Refused, source cancelled, no partial file |
| 21 MiB plus PDF structure | Bounded disk, missing length | Stream refused, cancelled, no partial file |
| 21 MiB plus PDF structure | Bounded disk, understated length | Stream refused, cancelled, no partial file |

Nine Node unit tests passed for exact byte ceilings, cancellation, source errors, header handling, client binding and invalid configuration.

Python scratch tests cover unique jobs, exception cleanup, permission setup failure, invalid/missing/oversized output, hard-link refusal, sibling preservation, Windows junction rejection and cleanup, and an actual Windows ACL query. The POSIX-only symlink test is skipped on Windows; Windows junction cases run instead. The full offline suite ran **449 tests in 265.798 seconds: 448 passed and one was skipped**. Results are recorded in `tests/artifacts/lms-temporary-full-suite.log`. Python compilation, Node syntax checks and whitespace checks also passed. The existing Windows DPAPI test was run with approved permissions on synthetic data.

`scripts/experiment_lms_temporary.py` is opt-in (`--run`); without the flag it accesses no package/settings/services. Its Node child receives no IBM, bot, Google or school configuration. The actual tool receives a synthetic API client; its real entrypoint/authentication lifecycle is not started. The experiment records metadata-only results in Git-ignored `tests/artifacts/lms-temporary-probe.json`, and checks the upstream source hash is unchanged.

## Boundaries before a production release

1. Decide whether to adopt hybrid inline plus backend-controlled temporary reading, and whether the 20 MiB candidate is sufficient for the demo materials.
2. Integrate the bounded wrapper into the existing authenticated connector without changing its owner binding or upstream-managed authentication. Do not merely pass `downloadPath` through the current policy.
3. Retain authorized extracted text/provenance without sending the temporary binary into the existing persistent original-file cache. Recheck access before publishing practice content.
4. Generalize and test the fixed filename/type handling for DOCX/PPTX if included. This experiment specifically proves large PDF handling.
5. Test meaningful typed source failures and LLM navigation through realistic paginated catalogs, then obtain deployment approval and perform an owner live test.

The existing deployed owner failure remains unresolved until those integration and acceptance steps are completed. The working nonowner upload/quiz flow is unchanged.
