# AI5.3 Production Media Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fail-closed malicious-file admission scanning of verified quarantine bytes before image decoding or persistence.

**Architecture:** A dependency-free core scanner port returns structured verdicts. Composition injects a bounded clamd adapter in staging/production or an explicitly selected fake in local/test; `disabled` means the whole media upload capability is off, never a clean-scan bypass. Ingestion accepts only `CLEAN` before Pillow, stage, or finalize.

**Tech Stack:** Python 3.12, Protocol/dataclasses, existing media UoW, clamd streaming protocol, pytest, Ruff, Mypy.

## Global Constraints

- Core has no ClamAV, Pillow, FastAPI, persistence, or OpenHands dependency.
- Scan after quarantine integrity confirmation and before validation/decode.
- Staging/production require clamd and fail closed if disabled, missing, or fake.
- Local/test behavior is explicit; `disabled` turns off the media upload capability and cannot stand in for fake-clean.
- Non-clean outcomes never stage/finalize and always close/delete/reject.
- Public errors contain stable codes only.
- Do not modify OpenHands, scoring, Monad, frontend, retention, janitor, or natural-image semantics.

---

### Task 1: Core port and architecture boundary

**Files:** modify `media_core/ports.py`; test `tests/media_core/test_ingestion.py` and `tests/architecture/test_media_import_boundaries.py`.

**Interfaces:** produce six-state `MalwareScanStatus`, frozen `MalwareScanVerdict`, and `MalwareScanner.scan(ReadOnlyMediaSource)`; no raw ClamAV/path/payload fields.

- [ ] Write RED tests for clean, malicious, unavailable, timeout, error, and unknown construction, invalid engine/status/metadata, stable error mapping, and forbidden core imports.
- [ ] Run the two exact test files and confirm missing-type RED failures.
- [ ] Add the minimal dependency-free models/protocol from the spec.
- [ ] Re-run and require PASS before proceeding.

### Task 2: Ingestion ordering and atomic cleanup

**Files:** modify `media_core/ingestion.py`, `media_application.py`, and `tests/media_core/test_ingestion.py`.

**Interfaces:** consume the scanner; produce sanitized malicious/unavailable application errors.

- [ ] Write RED ordering proof: finalize quarantine, verify facts, scan, then validate.
- [ ] Add clean PNG, EICAR, unavailable, timeout, unknown, raises, and cancellation RED cases.
- [ ] In every non-clean case assert no validator/decode, stage, transaction finalize, mark, or confirm; assert stream close, quarantine delete/close, lease reject, and primary-error preservation.
- [ ] Inject the scanner, accept only `CLEAN`, sanitize all adapter failures, and preserve cancellation through `finally` cleanup.
- [ ] Run `.venv/bin/python -m pytest agent-server/tests/media_core -q`; require PASS.

### Task 3: Bounded clamd adapter

**Files:** create `media_adapters/clamd_malware_scanner.py` and `tests/media_adapters/test_clamd_malware_scanner.py`; touch lock/build files only if required.

**Interfaces:** consume read-only source plus endpoint/timeouts/max bytes/concurrency; produce core results only.

- [ ] Build a deterministic fake clamd protocol server for `OK`, `FOUND`, `ERROR`, malformed, empty, delayed, and disconnected responses.
- [ ] Add RED clean PNG and EICAR tests; only `OK` may be clean.
- [ ] Add RED unavailable, timeout, unknown, malformed, empty, EOF, and raised-I/O tests; raw text must not escape.
- [ ] Add exact-limit and one-byte-over tests; over-limit transfers no bytes.
- [ ] Add bounded-concurrency plus queued and in-flight cancellation tests proving close and capacity release exactly once.
- [ ] Implement the minimal streaming adapter without caller paths, shell execution, hidden retries, or unbounded waits.
- [ ] Run the adapter test file and require PASS.

### Task 4: Explicit profile policy and fakes

**Files:** modify `config/profiles.py`; create `media_adapters/fake_malware_scanner.py`; add focused config/fake tests.

- [ ] Write RED profile matrix: staging/prod missing, disabled, or fake fails at startup; valid clamd passes.
- [ ] Write RED local/test matrix: explicit clamd or deterministic fake passes when selected; disabled turns off upload capability with zero side effects or stable `media_disabled`, and fake-clean is never a disabled fallback.
- [ ] Add deterministic fake clean, malicious, unavailable, timeout, error, unknown, and raises tests; prove staging/production cannot select fake or disabled.
- [ ] Implement frozen `MediaSecurityPolicy` with endpoint, connect/total/admission timeout, max bytes, and max concurrency. Prevent secret leakage in repr/log serialization.
- [ ] Run focused config/fake tests and require PASS.

### Task 5: Composition and protected duties

**Files:** modify `bootstrap/media_composition.py`; test architecture and media build/import contracts.

- [ ] Write RED composition tests for production clamd, production fake rejection, local explicit fake, and missing settings.
- [ ] Add RED architecture tests forbidding scanner/clamd imports or decisions in OpenHands runtime/adapter, scoring, plugins/Monad, and frontend-facing runtime paths.
- [ ] Wire lazy adapter selection into `MediaIngestionService`, preserving media-disabled imports; invalid staging/prod policy must stop composition, and disabled must not register the upload endpoint or construct a fake-clean scanner.
- [ ] Run architecture and build/import tests and require PASS.

### Task 6: Stable API boundary

**Files:** modify `api/media_routes.py` and `tests/api/test_image_evidence.py`.

- [ ] Write RED malicious/unavailable/timeout/unknown tests containing fake paths, endpoints, signatures, and raw clamd text.
- [ ] Assert responses contain only `media_malicious` 422/non-retryable or `media_scan_unavailable` 503/retryable and never diagnostic substrings.
- [ ] Add explicit mappings while retaining the generic 500 fallback.
- [ ] Run the API test file and require PASS.

### Task 7: Integration and guarded clamd proof

**Files:** create `tests/integration/test_media_malware_admission.py`; update build/deployment configuration only as needed.

- [ ] Add deterministic composed clean PNG and EICAR flows using a fake daemon boundary.
- [ ] Cover unavailable, timeout, unknown, raises, cleanup, no stage/finalize, and cancellation.
- [ ] Add a guarded real-clamd test skipped unless explicit endpoint and enable flag are present.
- [ ] Run deterministic tests without network and record guarded staging evidence without claiming production acceptance.

### Task 8: Documentation and acceptance

**Files:** update `.env.example`, staging runbook, gate report, and task board only after implementation evidence exists.

- [ ] Document explicit configuration, health checks, rollout, rollback-by-disabling-upload, and stable codes.
- [ ] Do not claim AI5.3 implementation or acceptance until every required gate passes.
- [ ] Run exact acceptance commands:

```bash
cd /home/holy/web3/focusproof-agent
.venv/bin/python -m pytest agent-server/tests/architecture/test_media_import_boundaries.py agent-server/tests/media_core agent-server/tests/media_adapters agent-server/tests/api/test_image_evidence.py agent-server/tests/integration/test_media_malware_admission.py -q
.venv/bin/python -m ruff check agent-server/focusproof/media_core agent-server/focusproof/media_adapters agent-server/focusproof/bootstrap/media_composition.py agent-server/focusproof/config/profiles.py agent-server/focusproof/api/media_routes.py agent-server/tests/architecture agent-server/tests/media_core agent-server/tests/media_adapters agent-server/tests/api/test_image_evidence.py agent-server/tests/integration/test_media_malware_admission.py
.venv/bin/python -m mypy agent-server/focusproof/media_core agent-server/focusproof/media_adapters agent-server/focusproof/bootstrap/media_composition.py agent-server/focusproof/config/profiles.py agent-server/focusproof/api/media_routes.py
git diff --check
```

- [ ] Verify the diff stays within the spec's allowed files and protected duties remain untouched.

## Corrective security gate evidence (2026-08-14)

- Strict clamd NUL framing rejects EOF, LF/CRLF, missing terminators, multiple
  frames, and trailing bytes.
- One monotonic deadline covers semaphore admission, connect, stream transfer,
  and response.
- HTTP cancellation propagates through a cooperative commit gate; worker
  cleanup may finish, but cancellation observed before the persistence gate
  prevents stage, finalize, confirm, and successful facts.
- Staging requires an external clamd endpoint and has no fake/disabled fallback.
- Guarded real-clamd validation requires both clean and standard EICAR verdicts
  and deletes temporary probes.
- Deterministic code gate: 288 focused and 341 expanded tests passed; Ruff/Mypy clean.
- Real clamd/EICAR gate: BLOCKED_REAL_CLAMD (not executed; no authorized daemon).

## Migration strategy

No database migration. Deploy/health-check clamd first, configure with upload disabled, run deterministic and guarded staging gates, then enable staging. Rollback disables upload, never scanning.
