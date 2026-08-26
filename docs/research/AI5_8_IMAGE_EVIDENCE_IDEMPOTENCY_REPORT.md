# AI5.8 Image Evidence Idempotency Report

Status: READY_FOR_AI0_REREVIEW

## Root cause

The visible image list renders authoritative `session.state.evidence` records keyed by `evidenceId`; it does not manufacture a duplicate display row. The existing React `busy` state already suppresses ordinary sequential double events after render, and the backend already atomically replays a stable `(owner_id, session_id, idempotency_key)` reservation. The reproducible identity break was an unknown network result followed by reload/remount: the component-only UUID disappeared, so reselecting the same file and explanation generated a new key. The backend correctly treated that as a new intent and could create a second evidence row. A synchronous ref guard was also added so handler re-entry cannot cross the async fingerprint boundary.

AI0's first review found two additional identity breaks in the initial implementation. It computed one fingerprint and used one storage slot for a whole selected batch, so a first file could succeed while a later unknown file had no independently recoverable identity. It also built the HTTP key as `baseKey-filename-size`, which made rename retries diverge and equal-name/equal-size different contents collide. The first report's batch-recovery claim was therefore premature; this revision records the corrective RED/GREEN cycle.

## Design

The implementation keeps the existing multipart `idempotency_key`, repository/UoW reservation, deterministic scan/receipt identities, evidence ID, synchronizer, and OpenHands native EventLog. It adds no second conversation, event log, runtime, or public response type.

Immediately before each single-file HTTP request, the browser computes that file's intent fingerprint from owner, session, normalized explanation, MIME, size, and SHA-256 of its bytes. Each fingerprint has an independent storage key `focusproof:image-intent:v1:{sessionId}:{intentFingerprint}`. Its value has exactly `{schemaVersion, ownerUserId, sessionId, intentFingerprint, baseKey, createdAt}` and never stores file bytes, filename, or explanation text. Reads validate the exact six-field shape, owner, session, fingerprint, 24-hour TTL, and clock direction; invalid data is removed fail-safe.

The HTTP idempotency key is `img_` plus a SHA-256 digest of `baseKey:intentFingerprint`: fixed at 68 characters, filename-free, and below the backend's 255-character limit. Same bytes/MIME/size/explanation after rename recover the same unknown intent; equal filename and size with different bytes produce different keys. Each successful file clears only its own pending record before the batch advances. Timeout, HTTP 503, and other unknown/retryable results retain only the active record. A deterministic non-retryable result clears it. With no pending record, a later deliberate action receives a fresh base key, so global content deduplication is not introduced.

## RED / GREEN evidence

Initial RED established that remount after an unknown result changed the UUID-derived identity. The first GREEN reached 12/12 but did not correctly cover partial batches or multiple pending intents.

Corrective RED was a valid executable suite: 14 tests passed and 4 business assertions failed. The failures proved (1) second-file identity changed after first-file success and remount, (2) two unknown intents collapsed into one session slot, (3) rename changed the request key, and (4) equal-name/equal-size different bytes received the same key. The deterministic-failure test was corrected to assert the component's existing `status` behavior before implementation; syntax or test-structure failures were not counted as RED.

Corrective GREEN passes 18/18 component contracts. Coverage includes click and direct synchronous double-submit suppression, retryable failure, partial-batch resume, two independently recoverable pending intents, rename stability, content distinction, six-field privacy, corrupt/expired storage cleanup, fixed bounded request keys, success creating a later new intent, and deterministic failure creating a later new intent.

The real backend concurrency contract uses two independent `httpx.AsyncClient` instances released by one `asyncio.Event`. Both same-key requests return 200 and the same evidence ID. SQL counts exactly one evidence, reservation, scan attempt, clean receipt, and `evidence.submitted` audit projection. Two synchronizer calls still leave exactly one native OpenHands `MessageEvent` with stable key `evidence:{id}`.

## Verification commands

- `npm test -- --run tests/ImageEvidenceForm.test.tsx`: RED 14 passed / 4 failed, then GREEN 18 passed.
- `npm test -- --run tests/ImageEvidenceForm.test.tsx tests/api-boundary.test.ts tests/session-review.test.tsx`: 67 passed.
- `npm run lint`: passed with no findings.
- `npm run typecheck`: passed with no findings.
- `npm run build`: passed; optimized production build completed.
- `npx playwright test e2e/image-evidence.spec.ts`: 8 passed across Chromium, desktop-1280, mobile, and mobile-360.
- `.venv/bin/pytest -q agent-server/tests/api/test_image_evidence.py::test_concurrent_same_key_has_one_persistent_and_native_side_effect`: 1 passed.
- `.venv/bin/pytest -q agent-server/tests/api/test_image_evidence.py agent-server/tests/persistence/test_media_uow.py agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py`: recorded in final AI0 handoff.
- `.venv/bin/ruff check agent-server/focusproof agent-server/tests/api/test_image_evidence.py`: recorded in final AI0 handoff.
- `.venv/bin/mypy --strict agent-server/focusproof`: recorded in final AI0 handoff.
- `git diff --check`: recorded in final AI0 handoff.

No test was skipped, xfailed, weakened, or removed for this task. Environment-gated PostgreSQL tests retain their existing skip behavior.

## Risks

Session storage deliberately does not retain file bytes. After reload, the browser security model requires the user to reselect the file; matching bytes and explanation then recover the pending identity safely. A storage write failure remains fail-open for usability but backend idempotency still protects any request that retains its key during the page lifetime.

## Changed files

- `frontend/features/evidence/ImageEvidenceForm.tsx`
- `frontend/features/evidence/EvidencePanel.tsx`
- `frontend/features/session/SessionWorkspace.tsx`
- `frontend/tests/ImageEvidenceForm.test.tsx`
- `agent-server/tests/api/test_image_evidence.py`
- `docs/research/AI5_8_IMAGE_EVIDENCE_IDEMPOTENCY_REPORT.md`
