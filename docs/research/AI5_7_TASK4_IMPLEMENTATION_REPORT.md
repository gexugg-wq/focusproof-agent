# AI5.7 Task 4 Implementation Report

## Scope and frozen boundaries

This change implements only Task 4: quarantine TTL and permissions, janitor/recovery safety, and Pillow decoder process isolation. Task 1-3 scan audit, clean receipt, ingestion, production fail-closed profile, and neutral media metadata contracts remain intact. Task 5 safe-fact/OpenHands event work was not started.

No Manager, Factory, Synchronizer, ResultExtractor, scoring, Monad, or agent-loop code was changed. No OpenHands Runtime, Conversation, EventLog, Action, Observation, or Tool type was copied or reimplemented; the implementation is storage and decoder infrastructure around the existing official SDK boundary.

## Design rulings

- TTL boundary: expiry is inclusive. For a 60-second TTL, age 59 seconds is live; exactly 60 seconds and 61 seconds are expired. The comparison is `now >= quarantine_expires_at` after conversion to aware UTC.
- Receipt consistency: `MediaCleanReceipt.quarantine_expires_at` is taken from the finalized quarantine object instead of recomputing a second timestamp.
- Fix Round 1 receipt state machine: untrusted bytes first enter a short-TTL `untrusted-scan-spool`. The scanner reads only that spool. A clean verdict commits a clean attempt plus a pending clean receipt intention that has no usable quarantine capability. Only then is the spool promoted into formal quarantine, and a second transaction activates the clean receipt with the final quarantine id and expiry. Validation, normalization, decoder use, and safe media facts occur only after the ACTIVE clean receipt is committed.
- POSIX permissions: managed quarantine directories are forced to `0700`; payload, metadata, and decoder-job input files are `0600`. Ownership, regular-file type, symlink state, and resolved containment are checked before access or deletion.
- Atomicity: formal quarantine uses payload, record, and commit-marker files. Payload and record are durable-published first; the commit marker is the visible capability point. Readers accept an entry only when payload, record, and marker all exist and id, receipt hash, digest, byte size, path, mode, and resolved containment match. The implementation does not claim cross-rename atomicity. Any injected failure during payload fsync, payload directory fsync, record fsync, record directory fsync, marker fsync, or marker directory fsync removes visible marker state and `.part` files; invisible orphans are recoverable by janitor.
- Janitor: expiry cleanup is store-owned and accepts no caller path. It examines only validated records under the fixed managed root, never follows symlinks, preserves malformed/unsafe entries, reports sanitized entry/type diagnostics, and is idempotent under missing-file races.

## Decoder process model and IPC

`decoder_worker.py` is a standalone worker launched with a new OS process using the current Python executable and isolated mode. The parent module does not import Pillow. The child receives one bounded length-prefixed JSON request over stdin containing only operation, private job-root/input paths, byte size, digest, declared/validated media type, output limit, and test-only delay. It starts with a minimal environment and clears it before decode. It returns one bounded length-prefixed JSON response over stdout with primitive metadata. Normalized bytes are written to a private `0600` job file; stdout carries only basename, size, and sha256.

The parent verifies that the worker PID differs from its own, caps request and response frames at 16 KiB, caps stderr at 8 KiB, rejects malformed/truncated/oversized frames, and validates normalized output basename, resolved path, mode, size, and digest before reading it. Startup failure, timeout, IPC EOF, malformed IPC, oversized stdout/stderr, worker rejection, parent exception, and parent cancellation all converge on `finally`: a live child process group is killed and reaped and the private decoder job directory is removed. There is no thread, async-task, or in-process Pillow fallback. The adapter exposes the last worker PID only as diagnostic state; it is not added to neutral media facts.

Pillow safety policy remains: PNG/JPEG/WebP only; single-frame only; strict terminal/container validation; maximum axis 12,000; maximum 40,000,000 pixels; maximum 160 MiB RGBA decode budget; decompression-bomb warnings/errors are rejected; verification is followed by a reopened full load; corrupt/malicious input never yields validated metadata.

## Recovery and replay invariants

- Expired quarantine payload/record pairs are removed once and cannot be reopened.
- Active entries remain available before their absolute expiry; exactly-at-expiry access fails closed.
- Stale decoder job directories are recoverable only when they have the owned name prefix, private directory/file modes, regular-file contents, old mtime, and resolved containment. Recent, symlinked, malformed, and external entries are preserved.
- Repeated sweeps are safe and return no duplicate recovery.
- Decode failures do not emit safe metadata. The existing ingestion idempotency and clean-receipt gate remain the only route into validation/normalization.
- Replay/idempotency returns existing finalized outcomes without re-decoding, and expired or non-active quarantine capabilities cannot be reopened.

## TDD evidence

Initial frozen-plan baseline before new Task 4 assertions: `79 passed`. New RED produced 7 expected failures covering TTL API/boundary, POSIX modes, permission drift, expiry sweep, distinct worker PID, timeout cleanup, and startup cleanup. Recovery RED then failed collection because `recover_orphan_decoder_jobs` did not exist. GREEN results:

- Task 4 store/codec/recovery files: `90 passed`.
- Task 1-3 receipt/ingestion/security/composition/architecture regression: `226 passed`.
- Neutral metadata regression exposed three failures when worker PID was initially placed in public attributes; PID was moved to adapter diagnostic state. Relevant regression: `38 passed`.
- Full agent-server suite: `1672 passed, 7 skipped, 14 deselected, 0 failed`.

Additional failure paths covered: corrupt and unsupported payloads, decompression-bomb dimensions, worker timeout, worker startup failure, IPC EOF, parent cancellation, and stale orphan recovery without outside-root deletion.

## Fix Round 1 evidence

AI0 rejected the first Task 4 implementation for four findings. RED tests were added before implementation for each:

- P1-A: formal quarantine now has fault injection for payload fsync, payload directory fsync, record fsync, record directory fsync, commit-marker fsync, and commit-marker directory fsync. Each failure leaves no visible commit marker and no `.part` files. Recovery removes old invisible record/payload orphans.
- P1-B: ingestion tests prove scanner reads `spool.open`, formal `quarantine.promote` happens only after `clean_receipt.pending`, and validation starts only after `clean_receipt.record`. Pending receipt failure never promotes; active receipt failure cleans promoted formal quarantine before decode.
- P1-C: codec tests cover oversized response length, infinite stdout, infinite stderr, truncated frames, forged normalized path, digest mismatch, oversized normalized output, and bounded job-file output instead of base64 stdout.
- P2-D: Linux process tests cover real timeout, IPC EOF, startup failure, parent exception/cancellation, and a real parent SIGINT while a worker child exists; child PID disappears and job root is empty.

Fresh GREEN results after Fix Round 1:

- Task4 store/codec/recovery + ingestion/persistence focus: `189 passed`.
- Task4 + architecture/import + Task1-3 receipt/security/composition regression: `336 passed`.
- Full `agent-server/tests`: `1692 passed, 7 skipped, 14 deselected, 470 warnings, 0 failed`.
- Ruff: all checks passed.
- Strict mypy: success, 117 source files.

## Static and repository gates

- Ruff over `agent-server/focusproof`, `agent-server/tests`, and `scripts`: all checks passed.
- Strict mypy over `agent-server/focusproof`: success, 117 source files.
- The fresh-process media-disabled import test remains part of the architecture/API regression and passes.
- `git diff --check`, staged-empty, and bridge-zero are run as final handoff gates after this report is installed.

## Fix Round 2 evidence

AI0 rejected Fix Round 1 for two remaining P1 findings. Fix Round 2 stays inside Task 4 and changes only quarantine durability/recovery, pending receipt replay, persistence schema, and tests/reporting.

- P1-A durable rollback: formal quarantine rollback now withdraws the unique visibility point first: commit marker, then record, then payload. Cleanup failures are no longer silently swallowed. The store raises a structured `QuarantinePublishError` with sanitized `RollbackFailure` entries containing only artifact id, managed target kind, operation, and exception type. Every actually removed file attempts to fsync its parent directory; parent fsync failure is reported as a recoverable rollback orphan. Recovery now also scans old `commits/*.commit` markers and removes incomplete marker/record/payload triplets, including marker-only, marker+record-missing-payload, and marker+payload-missing-record cases, without touching a valid three-file triplet or following caller-provided paths.
- P1-B recoverable pending replay: `PendingCleanReceipt` now persists the minimum replay capability: receipt id, attempt id, artifact digest, receipt hash, spool token, spool size, spool sha256, spool expiry, target quarantine expiry, and created_at. The repository can read pending clean receipt state by the stable scan idempotency key `session_id:idempotency_key:fingerprint`. Ingestion checks this state before reserving/streaming/scanning on retry. Replay reopens only a managed spool whose token, digest, size, mode, expiry, and containment match, then idempotently promotes and activates. If promotion already succeeded but active receipt commit failed, the store discovers the existing complete formal triplet by receipt id/hash/digest and activation can continue without duplicating scan attempts or formal artifacts.
- Pending transient failures are retryable: promotion failure and active receipt commit failure now raise `MediaPromotionPendingError`, a retryable pending-state error. These paths do not terminally reject the media lease and do not delete the persisted spool/formal state needed for replay. Truly unrecoverable spool loss, tampering, or expiry remains fail-closed and cannot start validation/decoder/safe facts.
- Schema migration: SQLite upgrade/downgrade/re-upgrade and PostgreSQL offline DDL include the new pending replay fields. The migration still imports only the frozen scan contract and does not import `media_core`.

Fix Round 2 RED/GREEN commands:

- Focused new/relevant set after implementation: `32 passed`.
- Task 4 store/codec/recovery files: `110 passed`.
- Task1-3 persistence/ingestion/import/architecture regression: `206 passed, 6 skipped`.
- Full `agent-server/tests` from repo root with `PYTHONPATH=agent-server:.`: `1698 passed, 7 skipped, 14 deselected, 470 warnings, 0 failed`.
- Ruff over `agent-server/focusproof`, `agent-server/tests`, and `scripts`: all checks passed.
- Strict mypy over `agent-server/focusproof`: success, 117 source files.
- `git diff --check`: passed.
- `git diff --cached --name-only`: empty.
- Temporary Fix Round 2 bridge directories: removed and verified absent.

The Fix Round 1 statement that active receipt failure cleaned promoted quarantine before decode is superseded by Fix Round 2: promoted formal quarantine is preserved only as an invisible/recoverable pending replay artifact until active receipt succeeds or janitor/recovery expires it. Validation, normalization, decoder, and safe facts still require an ACTIVE clean receipt.

## Fix Round 3 evidence

AI0 rejected Fix Round 2 for two P1 findings. Fix Round 3 remains limited to Task 4 recovery diagnostics and pending clean receipt replay/concurrency.

- P1-A recovery diagnostics: quarantine recovery now emits bounded structured diagnostics instead of swallowing cleanup failures. Each diagnostic contains only a validated/sanitized artifact id, managed target category, operation, exception type, and retryable flag. It does not include absolute paths, exception messages, environment values, or source payload content. Recovery deletion is fail-closed and idempotent: if marker, record, payload, part unlink, or parent directory fsync fails, the operation reports a retryable diagnostic and does not report the artifact as recovered; a later sweep can retry and then a third sweep is a no-op. Recovery keeps the marker visibility point first, does not follow symlinks, does not delete outside the managed root, and does not remove valid three-file triplets.
- MediaJanitor diagnostics: `MediaJanitor.last_diagnostics` now merges quarantine, object-store, and pending-clean sweeps in stable order with a hard 128-entry bound.
- P1-B constraint-backed get-or-create: scan attempts, pending clean receipts, active clean receipts, and same-key media reservation races now rely on database uniqueness constraints with savepoint/rollback winner reads. Concurrent losers validate the winner’s stable identity fields and continue through pending/idempotent replay rather than terminal rejection.
- Active receipt idempotency: active receipt creation uses the pending receipt’s stable `created_at`. Retry after an active commit or after-commit fault reads the existing active receipt by identity instead of conflicting on non-identity timestamp drift.
- Pending orphan janitor: the scan-audit repository lists expired pending clean receipts in bounded pages while excluding rows that already have an active receipt. Janitor cleans the managed spool/formal orphan through the quarantine store and then deletes the DB pending intention. Active receipts are not listed for cleanup, so their formal artifacts are preserved.
- Real SQLite evidence: tests cover a file-backed SQLite DB with a new engine, new UoW factory, and new `MediaIngestionService` simulating process restart after active receipt commit succeeded but the caller received an after-commit fault. The retry skips scanner/decode-before-active, resumes from pending/active state, and leaves exactly one attempt, one pending receipt, and one active receipt.
- Real same-key concurrency evidence: a multi-thread SQLite file-DB ingestion test exercises the full chain with two same-key callers. Losers do not terminally reject; replay/NOOP cleanup handles the race; final DB state has one scan attempt, one pending receipt, and one active receipt.
- PostgreSQL evidence: PostgreSQL offline DDL/migration checks still run in the persistence regression. No local `FOCUSPROOF_TEST_POSTGRES_MEDIA_URL` was used in this Fix Round 3 run, so a live PostgreSQL same-key concurrency gate remains a Task 6/environment gate rather than being claimed here.

Fix Round 3 RED/GREEN and gate commands:

- New RED tests initially failed for recovery diagnostics, concurrent attempt get-or-create, active receipt created_at drift, and missing pending orphan APIs.
- New diagnostic/recovery and repository tests: `9 passed`.
- Real SQLite restart/concurrency ingestion tests: `2 passed`.
- Task 4 store/codec/recovery files: `116 passed`.
- Task 4 + ingestion/persistence focused slice: `228 passed`.
- Task1-3 persistence/migration/import regression: `253 passed, 6 skipped, 436 warnings`.
- Full `agent-server/tests` from repo root with `PYTHONPATH=agent-server:.`: `1710 passed, 7 skipped, 14 deselected, 470 warnings, 0 failed`.
- Ruff over `agent-server/focusproof`, `agent-server/tests`, and `scripts`: all checks passed.
- Strict mypy over `agent-server/focusproof`: success, 117 source files.

## Residual risks

- Parent still reads verified normalized output bytes into memory after checking size and digest because the existing normalizer interface returns a seekable stream. The worker no longer serializes normalized bytes through stdout, and the inherited max decoded/output limits bound memory.
- Hard machine termination can leave a private decoder job directory until recovery runs. The recovery function safely reclaims only old, structurally valid managed jobs.
- Filesystems that do not support directory fsync retain the existing explicitly tolerated `EINVAL`/`ENOTSUP` behavior; other durability errors remain visible and fail closed.

## Fix Round 4 evidence

Fix Round 4 remains limited to Task 4 concurrency ownership and durable quarantine recovery.

- Formal quarantine ownership: an ACTIVE clean receipt now retains its deterministic formal payload/record/commit capability until `quarantine_expires_at`; ordinary ingestion callers close but never delete the shared triplet. Only caller-created, unpromoted spool and staged objects are caller-cleaned.
- Promotion linearization: the formal id remains a stable hash of receipt identity. An owner-only, durable filesystem exclusive promotion claim serializes cross-process publication; followers wait for and reopen the winner's completed triplet. Claims and losing spools are removed after convergence, and no process lock or in-memory singleflight is used.
- SQLite same-key finalization: SQLite uses a database-backed write-CAS boundary on the owned learning-session row before media reservation reads. A follower reaching an existing pending MARK receives an ABORT for its own staged object, cleans only that object, reconciles the winner's persisted MARK/completion, and returns the same product result. PostgreSQL continues to use row locking/constraints; no live PostgreSQL result is claimed in this round.
- Stable scan replay: concurrent attempts and pending receipts adopt the constraint winner's stable identity rather than treating timestamp or spool-token drift as a terminal conflict.
- Durable deletion confirmation: recovery writes an owner-only bounded intent journal before touching marker, record, payload, or part targets. Intent file fsync, atomic rename, and journal-directory fsync precede unlink. Target absence and target-directory fsync precede a durable done record and journal cleanup. If target or journal cleanup durability fails, a restart replays the retained intent/done record; no unlink is described as rollback-capable.
- Recovery safety: journals are processed before orphan scans; record fields map only to fixed managed directories and stable basenames. Symlinks, traversal, malformed/oversized records, permission drift, and outside-root targets fail closed with bounded structured diagnostics containing no absolute path or exception message. Valid formal triplets remain untouched.

Fix Round 4 RED/GREEN and final gates:

- Initial deterministic RED: same-key callers produced `MediaPromotionPendingError`/`InvalidMediaReferenceOutcomeError`, and parent-directory fsync failure left no second-run recovery object (`2 failed`).
- Barrier-driven real SQLite concurrency: `100 passed` using a real file DB, two threads, distinct service/UoW instances, and barriers at promotion, validation, and finalize. Every round returned one shared result, one attempt, one pending receipt, one active receipt, one formal triplet, one product artifact, zero rejects/exceptions, readable pre-expiry formal bytes, empty spool/claim state, and loser staged cleanup.
- Deletion journal restart fault matrix: `32 passed` across marker/record/payload/part and intent write/fsync/rename/dir-fsync, unlink, target-dir-fsync, journal unlink, and journal cleanup-dir-fsync failures. The additional focused journal contract file finished `83 passed`.
- Task 4 store/codec/recovery/ingestion/persistence focus: `378 passed`.
- Task 1-3 persistence/ingestion/migration/import regression: `533 passed, 436 warnings`.
- Full `agent-server/tests`: `1860 passed, 7 skipped, 14 deselected, 470 warnings, 0 failed`.
- Ruff over `agent-server/focusproof`, `agent-server/tests`, and `scripts`: all checks passed.
- Strict mypy over `agent-server/focusproof`: success, 117 source files.
- Live PostgreSQL same-key concurrency remains the Task 6 environment gate and was not run or claimed here.

## Fix Round 5 implementation note

Round 5 stays inside the Task 4 media/persistence boundary and replaces formal quarantine publication ownership with durable SQL state.
The stable pending clean receipt identity remains the publication key: attempt id, receipt id, receipt hash, artifact digest, and scan idempotency key.
The pending clean receipt row is extended with publication status, deterministic formal artifact id, lease owner, lease expiration, version, and timestamps.
Publication ownership is acquired by inserting the pending receipt or by a single conditional SQL update from pending or stale publishing to publishing.
Followers do not wait on files, process locks, in-memory state, or sleep loops; they read the persisted state once and either replay published or surface retryable in-progress state.
The quarantine store no longer decides concurrency winners and no longer creates promotion claim files; it only writes or verifies the deterministic formal triplet for a caller that already owns publication.
Formal publication is idempotent: an existing triplet with matching receipt hash, digest, size, expiry, and metadata is success; a conflicting triplet is rejected fail-closed.
If the owner faults after claim commit but before the write, lease/CAS takeover lets a restart publish the same deterministic formal id from the persisted spool capability.
If the owner faults after the formal write but before published commit, the next owner verifies the triplet and commits published without duplicating artifacts.
If the owner faults after published commit but before response, replay reads the active receipt/product path and returns the existing result.
Ordinary ingest cleanup remains caller-owned only: unpromoted spool and staged objects are cleaned, while active formal quarantine is retained until expiry/janitor recovery.
Reference reconciliation adopts the same no-busy-wait contract: one DB replay/read per call, visible outcomes replay immediately, and unfinished outcomes return retryable pending state.
Deletion-journal recovery gains direct safety tests for the journal record itself: symlink, wrong mode, directory replacement, malformed/oversize/traversal, diagnostics redaction, and repeated restart idempotency.
No Manager, Factory, Synchronizer, ResultExtractor, scoring, Monad, agent loop, or OpenHands SDK runtime/type file is part of this round.
Live PostgreSQL same-key concurrency remains a Task 6/environment gate and is not claimed by this Task 4 fix round.

## Fix Round 5 RED/GREEN and final gates

Round 5 remains limited to Task 4 media/persistence publication, journal safety, tests, and this report.

- Initial RED coverage failed for the real gaps: missing DB-backed publication columns/repository CAS methods, no stale publication takeover, old sleep/promotion-claim protocol tokens, and missing direct deletion-journal record safety diagnostics.
- DB-backed publication state now lives on the pending clean receipt row with deterministic formal artifact id, publication status, owner, lease expiry, version, published timestamp, failure reason, and updated timestamp. Ownership uses conditional SQL updates/CAS and persisted lease takeover. Store code only performs idempotent deterministic formal write/verify; it no longer decides concurrency winners.
- Follower behavior is no-busy-wait: a single DB read either replays published state or returns retryable in-progress state. Failed/stale publishing can be taken over by a later owner through persisted lease/version state.
- Formal publication remains idempotent: matching existing formal triplets replay as success, while mismatched digest/metadata fails closed. Ordinary ingest cleanup remains caller-owned and does not delete active formal artifacts.
- Deletion-journal direct safety tests now cover the journal record itself as symlink, owner-mode drift, malformed/oversize/traversal content, and directory replacement/symlink TOCTOU. Diagnostics remain bounded and redacted; second restart recovery and third empty recovery are asserted.

Fresh Fix Round 5 evidence:

- New RED/GREEN focused protocol/CAS/journal tests: `10 passed`.
- Journal fault/safety matrix after final store changes: `32 passed`; full store/codec/recovery/ingestion/persistence Task 4 slice: `366 passed`.
- Real SQLite same-key 100-round pressure test, five independent final-code runs: run 1 `1 passed in 48.51s`, run 2 `1 passed in 48.44s`, run 3 `1 passed in 48.83s`, run 4 `1 passed in 48.04s`, run 5 `1 passed in 48.35s`.
- Task1-3 persistence/ingestion/migration/import/architecture regression: `311 passed, 436 warnings`.
- Full `agent-server/tests` from repo root with `PYTHONPATH=agent-server:.`: `1869 passed, 7 skipped, 14 deselected, 470 warnings, 0 failed`.
- Ruff over `agent-server/focusproof`, `agent-server/tests`, and `scripts`: all checks passed.
- Strict mypy over `agent-server/focusproof`: success, 117 source files.
- `git diff --check`: passed.
- Rejected publication-token grep over Task 4 media production/protocol paths: no hits.

Round 5 changed-file allowlist:

- `agent-server/focusproof/media_core/models.py`
- `agent-server/focusproof/media_core/ports.py`
- `agent-server/focusproof/media_core/ingestion.py`
- `agent-server/focusproof/media_adapters/local_quarantine_store.py`
- `agent-server/focusproof/media_adapters/decoder_worker.py`
- `agent-server/focusproof/media_adapters/pillow_image_codec.py`
- `agent-server/focusproof/persistence/models.py`
- `agent-server/focusproof/persistence/audit_projection.py`
- `agent-server/migrations/versions/0006_media_scan_audit_and_receipts.py`
- `agent-server/tests/media_core/test_ingestion.py`
- `agent-server/tests/media_adapters/test_store_janitor_contract.py`
- `agent-server/tests/persistence/test_media_scan_audit.py`
- `agent-server/tests/integration/test_media_malware_admission.py`
- `agent-server/tests/api/test_image_evidence.py`
- `docs/research/AI5_7_TASK4_IMPLEMENTATION_REPORT.md`

No Round 5 allowlist file is a Manager, Factory, Synchronizer, ResultExtractor, scoring, Monad, agent-loop, or OpenHands SDK runtime/type file. The worktree still contains earlier dirty changes outside this allowlist, including protected-path files from prior phases; this round did not reset, revert, clean, stage, commit, push, or merge them.

Live PostgreSQL same-key concurrency remains a Task 6/environment gate and was not run or claimed in Fix Round 5.

## Fix Round 5 closure evidence

Visible closure execution stayed inside the Task 4 media, persistence, test, and report boundary and removed the four temporary .orig bridge files left by the previous executor.

- P1 RED: test_task4_claim_window_harness_uses_two_party_barriers initially failed because the claim-window harness still exposed event-only owner_claimed and follower_observed coordination instead of explicit two-party barriers.
- P1 GREEN: the claim/publishing window now uses two Barrier(2) checkpoints. The first releases the follower only after the owner has committed the DB publishing lease; the second keeps the owner from publishing until the follower has observed that persisted publishing state. test_task4_claim_window_harness_uses_two_party_barriers plus the 100-round claim-window test passed: 2 passed in 62.20s.
- P0 audit: takeover order already matched the closure requirement before additional edits: DB lease CAS/commit, read-only deterministic formal triplet verification, spool fallback only when formal is absent, and fail-closed conflict/incomplete handling.
- During the first repeated published-replay run, the pressure test exposed a decoder IPC race: a fast worker could exit with code 0 while the parent broke out before draining stdout, producing decoder IPC response truncated. RED test_decoder_parent_drains_stdout_after_fast_worker_exit reproduced the parent-side race and then passed after the parent performed a final nonblocking stdout/stderr drain after worker exit.

Fresh closure verification:

- P0/fault matrix: 50 passed in 2.47s.
- Migration upgrade/downgrade/re-upgrade plus offline PostgreSQL DDL: 3 passed in 2.90s.
- Claim/publishing 100-round pressure, five final-code runs: 58.21s, 58.30s, 58.19s, 58.48s, and 60.06s, all passed.
- Published replay validation/finalize/stage 100-round pressure, five final-code runs after the IPC fix: 190.74s, 194.65s, 199.39s, 198.73s, and 204.63s, all passed.
- Journal/store safety matrix: 94 passed in 5.60s.
- Core Task4 slice: 275 passed in 327.62s.
- Ancillary media/API/integration slice: 202 passed, 6 skipped, 1 warning in 25.78s.
- Task1-3 media architecture/import/runtime regression: 219 passed, 1 skipped, 437 warnings in 48.44s.
- Full backend non-external suite: 1778 passed, 7 skipped, 14 deselected, 470 warnings in 772.03s.
- Ruff over agent-server/focusproof, agent-server/tests, and scripts: all checks passed.
- Strict mypy over agent-server/focusproof: success, 117 source files.

Live PostgreSQL same-key concurrency remains an environment gate and was not claimed in this Task 4 closure pass.

Closure repository hygiene after this report update: git diff --check passed before the hygiene note; cached diff was empty; find . -name *.orig returned no files; rejected old promotion-token grep returned no hits. The dirty tree still contains pre-existing protected-path changes outside the Task 4 closure allowlist, so this pass reports closure-scope boundary cleanliness rather than whole-worktree cleanliness.

## Fix Round 6 scope expansion pre-note

Round 6 remains inside Task 4 recovery/journal safety. The delegation named agent-server/focusproof/media_core/local_quarantine_store.py, but that file does not exist in the current WSL tree; the concrete LocalQuarantineStore implementation is agent-server/focusproof/media_adapters/local_quarantine_store.py. RED tests in agent-server/tests/media_adapters/test_store_janitor_contract.py reproduced the residual target/basename defect against that actual store, so this round must modify the adapter store file plus the corresponding store tests and this report.

No Manager, Factory, Synchronizer, ResultExtractor, scoring, Monad, agent loop, OpenHands runtime/type, Task 5, or Task 6 file is in scope for Round 6.

## Fix Round 6 RED/GREEN and final gates

Root cause: recovery deletion journals were keyed by artifact id and target kind, and journal payloads stored only a managed basename. The store validated artifact id, target kind, and owned regular-file status before writing the journal, but it did not prove the caller-supplied source path was the unique whitelist path for that target. Therefore a valid-hex malformed basename such as aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.part could create a stuck journal, and legacy target="part" could be asked to recover records, commits, or untrusted-scan-spool .part files while _target_path still mapped target="part" only to payloads/.{artifact_id}.part. That path/basename gap is the same failure mode behind the reviewer concern: recovery can report or journal work that is not the actual source file.

RED evidence before implementation:

- Focused RED command: .venv/bin/python -m pytest agent-server/tests/media_adapters/test_store_janitor_contract.py -k "valid_hex_part_without_managed_basename or legacy_part_target_rejects_non_payload_part_paths_without_journal".
- RED result: 7 failed, 150 deselected. Four failures showed valid-hex but invalid basename .part candidates were preserved while deletion-journal/*.json was written. Three failures showed target="part" returned True for records, commits, and untrusted-scan-spool candidates while the source file remained.

Implementation mapping:

- agent-server/focusproof/media_adapters/local_quarantine_store.py now validates the source path against _target_path(artifact_id, target, path.name) before any journal write. The resolved source path must equal the resolved whitelist path. A mismatch emits a sanitized validate diagnostic with retryable=False, writes no journal, and returns False.
- The existing directory-specific target kinds payload_part, record_part, commit_part, and spool_part remain the only non-legacy recovery targets for managed .part files. Legacy target="part" remains payload-only compatibility through _target_path and cannot delete, journal, or infer records, commits, or spool paths.
- Journal records still contain only artifact id, target kind, state, schema, and basename. No arbitrary relative or absolute caller path is written to the journal, and deletion still goes through journal replay rather than direct recovery unlink for recover_quarantine old parts.

GREEN evidence:

- RED-focused tests after fix: 7 passed, 150 deselected in 1.87s.
- Four managed-directory old .part recovery plus directory-specific fault/restart matrix: 52 passed, 105 deselected in 2.43s.
- Journal/store safety file: 157 passed in 5.56s.
- Claim/publishing 100-round pressure, one required run: 1 passed in 55.10s.
- Published replay validation/finalize/stage 100-round pressure, one required run: 1 passed in 181.57s.
- Task4 core slice excluding the two separately-run pressure nodes: 365 passed, 1 skipped, 2 deselected in 25.76s.
- Task1-3 media/runtime regression slice: 284 passed, 1 skipped, 1 warning in 57.41s.
- Full backend non-external/non-real-LLM suite from repo root: 1841 passed, 7 skipped, 14 deselected, 470 warnings in 717.55s.
- Ruff over agent-server/focusproof, agent-server/tests, and scripts: all checks passed.
- Strict mypy over agent-server/focusproof: success, 117 source files.
- Final hygiene gates after this Round 6 report update: git diff --check passed; cached diff empty; find . -name *.orig returned no files.

Exact Round 6 modified files:

- agent-server/focusproof/media_adapters/local_quarantine_store.py
- agent-server/tests/media_adapters/test_store_janitor_contract.py
- docs/research/AI5_7_TASK4_IMPLEMENTATION_REPORT.md

Boundary audit: all work was performed in WSL repository /home/holy/web3/focusproof-agent on branch agent/monad-evidence-plugin. The Windows D:\web3 directory was not used for code. No .env or secret file was read or modified. No reset, revert, checkout, clean, stage, commit, push, merge, or amend was performed. No Manager, Factory, Synchronizer, ResultExtractor, scoring, Monad, agent loop, OpenHands runtime/type, Task 5, or Task 6 source file was modified.

Residual risk: live PostgreSQL same-key media concurrency remains an external environment gate and is not claimed in this Task 4 round. The full backend suite still reports pre-existing deprecation warnings from FastAPI/Starlette, SQLAlchemy sqlite datetime adapters, and websockets legacy imports; no new failure is associated with Round 6. The worktree remains broadly dirty from prior phases, so Round 6 claims only the exact modified-file boundary above.


## Final Concurrency Fix: published replay reference convergence

AI5.7 Task4 final concurrency fix stayed inside the Task 4 media boundary and changed only ingestion reference reconciliation tests/reporting plus `agent-server/focusproof/media_core/ingestion.py`. No local object-store manifest validation was weakened, and `agent-server/focusproof/media_adapters/local_media_object_store.py` was not changed.

Read-only state-machine trace before implementation:

- DB reservation states: `ACTIVE` stores no durable media facts; `PENDING_REFERENCE` stores result JSON, staged object key, staged manifest id, canonical artifact id, and `intent_action`; `COMPLETED` plus evidence row is the durable visible fact.
- DB artifact states: the adopting reservation creates `media_artifacts.state = PENDING_REFERENCE`; `confirm_reference` moves that artifact to `REFERENCED`, completes the reservation as `ADOPTED`, and creates the evidence row.
- Replay states: `find_idempotent_outcome` returns `MARK_REFERENCED` while the same idempotency key is still pending and returns `NOOP/evidence_visible=True` after the reservation is completed.
- Object-store lifecycle: `stage` writes a binding manifest and staged file. `mark_referenced` verifies the manifest exactly, links/moves the staged object into `referenced`, removes the staged file, and durably unlinks the manifest. Manifest mismatch, invalid referenced object, or missing unproven object still fails closed.

Root cause: two replay/follower paths can observe the same durable `PENDING_REFERENCE/MARK_REFERENCED` outcome. One caller then successfully runs `mark_referenced` and `confirm_reference`, which is the linearization point for the artifact/reservation/evidence fact. A second caller may still try the stale `MARK_REFERENCED` intent after the winner has consumed/deleted the same staged manifest. The local store correctly raises `ValueError("staged manifest binding mismatch")`; ingestion previously converted that post-commit failure to retryable pending without re-reading the authoritative completed DB state, so an already-completed same fact could be misreported as pending.

Fix: post-commit `MARK_REFERENCED` failures now perform one authoritative replay read for the same owner/session/idempotency/fingerprint. They converge only when the reread is `evidence_visible=True`, its action is `NOOP`, its staged binding exactly equals the stale `MARK_REFERENCED` staged binding, the result object exactly equals the original durable result, and the result media id is the staged media id. Anything else, including different manifest id, different staged object key, different result, different identity, or incomplete pending state, keeps the previous fail-closed `PostCommitReferenceError` / retryable pending behavior.

TDD RED/GREEN:

- Deterministic RED `test_real_sqlite_published_replay_stale_mark_reference_race_converges` used real SQLite, real object store, two services/UoWs/engines, and barriers around follower finalize plus stale manifest read. Before the fix it failed at `local_media_object_store.py:245` with `staged manifest binding mismatch`, then `PostCommitReferenceError`, then `MediaReferencePendingError` from reconciliation.
- Negative guard `test_completed_replay_with_different_manifest_does_not_mask_mark_failure` proves a completed replay with a different manifest does not mask a real binding mismatch.
- GREEN focused result after implementation: `2 passed, 66 deselected in 1.35s`.
- Focused reference regression after implementation: `5 passed, 63 deselected in 1.22s`.

Fresh verification after the fix:

- Published replay validation/finalize/stage 100-round pressure, five consecutive final-code runs: `185.88s`, `233.81s`, `201.73s`, `210.43s`, and `199.32s`, all `1 passed`.
- Claim/publishing 100-round pressure: `1 passed in 62.37s`.
- Store/janitor contract including .part focused/legacy/replay and fault matrix: `157 passed in 5.91s`.
- Migration/offline PostgreSQL DDL: `91 passed, 436 warnings in 26.75s`.
- Task4 core slice excluding the two separately-run pressure nodes: `577 passed, 6 skipped, 2 deselected, 1 warning in 91.04s`.
- Task1-3 media/runtime regression slice: `257 passed, 1 skipped, 1 warning in 7.42s`.
- Full backend default suite: `1843 passed, 7 skipped, 14 deselected, 470 warnings in 907.59s`.
- Ruff over `agent-server/focusproof`, `agent-server/tests`, and `scripts`: all checks passed.
- Strict mypy over `agent-server/focusproof`: success, 117 source files.

Exact files modified by this final concurrency fix:

- `agent-server/focusproof/media_core/ingestion.py`
- `agent-server/tests/media_core/test_ingestion.py`
- `docs/research/AI5_7_TASK4_IMPLEMENTATION_REPORT.md`

Boundary audit: work was performed in WSL repository `/home/holy/web3/focusproof-agent` on branch `agent/monad-evidence-plugin`. Windows `D:\web3` was not used for code. No `.env` or secret file was read or modified. No reset, revert, checkout, clean, stage, commit, push, merge, or amend was performed. No Manager, Factory, Synchronizer, ResultExtractor, scoring, Monad, agent loop, OpenHands runtime/type, Task 5, or Task 6 source file was modified.

Residual risk: live PostgreSQL same-key media concurrency remains an external environment gate and is not claimed in this Task 4 fix. The full backend suite still reports pre-existing FastAPI/Starlette, SQLAlchemy sqlite datetime, and websockets legacy deprecation warnings. The worktree remains broadly dirty from prior phases, so this section claims only the exact final concurrency-fix boundary above.
