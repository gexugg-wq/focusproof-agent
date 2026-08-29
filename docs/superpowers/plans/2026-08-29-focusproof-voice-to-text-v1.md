# FocusProof Voice-to-Text Input V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-bounded tap-record, transcribe, edit, and manually submit workflow to the existing unified evidence composer without making raw audio or candidate transcripts part of Evidence, OpenHands, or scoring.

**Architecture:** A speech-only application boundary admits a metadata-only request before multipart parsing, reuses the lifespan-owned malware scanner and cross-worker scan slots, inspects audio in a sandboxed `mediainfo` process, and calls DashScope once under a 120-second deadline. The frontend recorder receives an ephemeral candidate transcript and inserts it into the existing textarea; the existing text Evidence submission remains the only persistence path.

**Tech Stack:** Python 3.12, FastAPI/Starlette ASGI, SQLAlchemy/Alembic, PostgreSQL production concurrency, SQLite single-process development, httpx, Clamd, bubblewrap + mediainfo, Next.js/React/TypeScript, MediaRecorder, Vitest/RTL, Playwright, official OpenHands SDK 1.31.0 unchanged.

**Spec:** `PLAN.md` and `PLAN-REVIEW-LOG.md`

## Global Constraints

- Run implementation and authoritative verification in WSL/Linux at `/home/holy/web3/focusproof-agent`.
- Reuse official OpenHands SDK 1.31.0 as-is; do not create audio OpenHands types, tools, events, or a second runtime.
- Do not modify public Evidence/scoring contracts or make raw audio/candidate transcript persistent.
- Use one provider attempt and one 120-second request-entry deadline, reserving seconds 115-120 for shielded cleanup.
- Production multi-worker speech requires PostgreSQL, Clamd, bubblewrap, and mediainfo. Missing prerequisites disable only speech.
- `qwen3-asr-flash` is the sole V1 ASR model. Never log or commit credentials, audio, transcripts, or upstream response bodies.
- Use the existing unified composer and existing Submit Evidence action. Do not create a separate voice card.
- Before each task, inspect current branch/worktree and preserve unrelated user changes. Commit only that task's owned files after its focused and regression gates pass.

---

### Task 1: Speech Domain Boundary, Configuration, and Capability

**Files:**
- Create: `agent-server/focusproof/speech_core/models.py`
- Create: `agent-server/focusproof/speech_core/ports.py`
- Create: `agent-server/focusproof/speech_core/errors.py`
- Create: `agent-server/focusproof/speech_core/__init__.py`
- Modify: `agent-server/focusproof/config/env.py`
- Modify: `agent-server/focusproof/api/app.py`
- Modify: `frontend/lib/api/contracts.ts`
- Test: `agent-server/tests/speech_core/test_models.py`
- Test: `agent-server/tests/api/test_speech_capability.py`

**Interfaces:**
- Produces: immutable `SpeechSettings`, `TranscriptionRequest`, `TranscriptionResult`, `AudioFacts`; protocols `SpeechTranscriptionProvider.transcribe(...)` and `AudioInspector.inspect(...)`; stable speech error hierarchy; enabled/disabled capability schema version 1.
- Consumes: existing environment/profile loaders and product-capability projection only.

- [ ] **Step 1: Write RED tests for settings and capability variants.**

```python
def test_missing_real_asr_configuration_disables_only_speech() -> None:
    capability = build_speech_capability(env={})
    assert capability == {
        "capabilityId": "speech_transcription",
        "schemaVersion": 1,
        "enabled": False,
        "reasonCode": "asr_not_configured",
    }

def test_enabled_capability_is_versioned_and_metadata_hint_is_explicit() -> None:
    capability = build_speech_capability(env=real_asr_env())
    assert capability["languageHintsAccepted"] == ["auto", "zh", "en"]
    assert capability["languageHintEffect"] == "metadata_only"
```

- [ ] **Step 2: Run focused tests and confirm import/capability failures.**

Run: `cd /home/holy/web3/focusproof-agent && .venv/bin/pytest agent-server/tests/speech_core/test_models.py agent-server/tests/api/test_speech_capability.py -q`

- [ ] **Step 3: Implement frozen models, protocols, settings, and capability projection.**

Use `Literal`/enums for formats, hints, states, and stable codes. Keep `speech_core` imports free of `domain.scoring`, Evidence, and `openhands_runtime`; add an architecture assertion test for this boundary.

- [ ] **Step 4: Run focused tests, Ruff, and strict MyPy.**

Run: `.venv/bin/pytest agent-server/tests/speech_core/test_models.py agent-server/tests/api/test_speech_capability.py -q && .venv/bin/ruff check agent-server/focusproof/speech_core agent-server/tests/speech_core agent-server/tests/api/test_speech_capability.py && .venv/bin/mypy agent-server/focusproof/speech_core`

- [ ] **Step 5: Commit Task 1.**

```bash
git add agent-server/focusproof/speech_core agent-server/focusproof/config/env.py agent-server/focusproof/api/app.py frontend/lib/api/contracts.ts agent-server/tests/speech_core agent-server/tests/api/test_speech_capability.py
git commit -m "feat(speech): define transcription boundary"
```

### Task 2: Metadata Ledger, State Constraints, Quotas, and Resource Slots

**Files:**
- Create: `agent-server/migrations/versions/0008_speech_transcription_requests.py`
- Modify: `agent-server/focusproof/persistence/models.py`
- Modify: `agent-server/focusproof/persistence/repositories.py`
- Modify: `agent-server/focusproof/persistence/unit_of_work.py`
- Test: `agent-server/tests/persistence/test_speech_migration.py`
- Test: `agent-server/tests/persistence/test_speech_repository.py`
- Test: `agent-server/tests/persistence/test_speech_postgres_concurrency.py`

**Interfaces:**
- Produces: `SpeechRequestRepository.admit`, `transition`, `mark_dispatching`, `finalize`, `recover_expired`; `ResourceSlotRepository.claim/release/reconcile`; immutable `SpeechAdmissionToken`.
- Consumes: Task 1 state/error types and existing UoW/session ownership model.

- [ ] **Step 1: Write RED migration and invalid-state matrix tests.**

Test upgrade/downgrade/re-upgrade and direct inserts that must fail: active without lease, terminal with lease, dispatching without dispatch timestamp, succeeded with outcome, empty slot with expiry, occupied slot missing work kind/token.

- [ ] **Step 2: Write RED repository tests for HMAC versions and duplicate semantics.**

Cover active and retained HMAC keys, missing historical key readiness failure, same key/different fingerprint conflict, active in-progress, succeeded result-unavailable, and explicit new-key behavior after terminal failure.

- [ ] **Step 3: Write RED PostgreSQL process-race tests.**

Use two independent engines/processes. Assert owner advisory lock is acquired before session lock, 20/session and 30/user/hour cannot overshoot, four ASR slots cannot become five, occupied retired slots drain, and stale lease generations cannot release/finalize.

- [ ] **Step 4: Implement migration, ORM, repositories, and UoW ports.**

Use canonical owner-then-session advisory locks with bounded statement/lock timeout. Use `BEGIN IMMEDIATE` for SQLite single-process tests. Store only metadata named in `PLAN.md`; add a schema inspection test rejecting transcript/audio-like columns.

- [ ] **Step 5: Run persistence gates.**

Run: `.venv/bin/pytest agent-server/tests/persistence/test_speech_migration.py agent-server/tests/persistence/test_speech_repository.py -q`

Run with disposable PostgreSQL: `.venv/bin/pytest -m postgres agent-server/tests/persistence/test_speech_postgres_concurrency.py -q`

- [ ] **Step 6: Commit Task 2.**

```bash
git add agent-server/migrations/versions/0008_speech_transcription_requests.py agent-server/focusproof/persistence agent-server/tests/persistence/test_speech_*.py
git commit -m "feat(speech): persist bounded request admission"
```

### Task 3: Shared Scanner Composition and Sandboxed Audio Inspection

**Files:**
- Create: `agent-server/focusproof/speech_adapters/mediainfo_inspector.py`
- Create: `agent-server/focusproof/speech_adapters/__init__.py`
- Modify: `agent-server/focusproof/bootstrap/media_composition.py`
- Modify: `agent-server/focusproof/media_application.py`
- Test: `agent-server/tests/speech_adapters/test_mediainfo_inspector.py`
- Test: `agent-server/tests/integration/test_shared_scan_slots.py`
- Fixtures: `agent-server/tests/fixtures/audio/`

**Interfaces:**
- Produces: `MediainfoAudioInspector.inspect(path, deadline) -> AudioFacts`; one lifespan-owned scanner and shared `ResourceSlotController` accepted by image and speech composition.
- Consumes: Task 1 `AudioInspector`, Task 2 resource slots, existing `MalwareScanner` and media command.

- [ ] **Step 1: Add RED format, malformed, timeout, output-limit, and sandbox-command tests.**

Fixtures must include minimal valid WebM/Opus, WAV, MP3 plus mismatched extension/MIME, multiple tracks, zero/over-limit duration, malformed/truncated samples, and oversized mediainfo output. Assert command uses no shell and includes bwrap network/filesystem/rlimit restrictions.

- [ ] **Step 2: Add RED mixed image/speech slot tests.**

Two independent app instances sharing PostgreSQL must never exceed configured scan slots when image and speech contend. Shrinking the configured pool must disable free surplus slots and drain occupied surplus slots.

- [ ] **Step 3: Implement bounded inspector and shared scanner injection.**

Production startup disables speech when `bwrap`, `mediainfo`, or Clamd is unavailable. Do not weaken existing image behavior. Refactor image composition to receive the already-built scanner/controller from lifespan.

- [ ] **Step 4: Run focused and existing media regressions.**

Run: `.venv/bin/pytest agent-server/tests/speech_adapters/test_mediainfo_inspector.py agent-server/tests/integration/test_shared_scan_slots.py agent-server/tests/media_adapters agent-server/tests/media_core -q`

- [ ] **Step 5: Commit Task 3.**

```bash
git add agent-server/focusproof/speech_adapters agent-server/focusproof/bootstrap/media_composition.py agent-server/focusproof/media_application.py agent-server/tests/speech_adapters agent-server/tests/integration/test_shared_scan_slots.py agent-server/tests/fixtures/audio
git commit -m "feat(speech): inspect and scan bounded audio"
```

### Task 4: DashScope Adapter and One-Deadline Transcription Service

**Files:**
- Create: `agent-server/focusproof/speech_adapters/dashscope_asr.py`
- Create: `agent-server/focusproof/speech_application.py`
- Test: `agent-server/tests/speech_adapters/test_dashscope_asr.py`
- Test: `agent-server/tests/speech_core/test_transcription_service.py`

**Interfaces:**
- Produces: `DashScopeSpeechTranscriptionProvider`; `TranscriptionService.execute(admission, upload, language_hint, disconnect_probe) -> TranscriptionResult`.
- Consumes: Tasks 1-3 ports/repositories/scanner/inspector/slots.

- [ ] **Step 1: Write RED exact-provider contract tests.**

Assert Beijing `/audio/transcriptions`, model `qwen3-asr-flash`, multipart file, no invented hint, transcript-only DTO, 256 KiB streamed response bound, blank/no-speech mapping, and redacted exceptions. Include fixtures with extra emotion/acoustic fields and prove they are discarded.

- [ ] **Step 2: Write RED lifecycle/crash/cancellation tests.**

Inject a monotonic clock and failures after admission, upload, scan, inspect, dispatch commit, provider response, and success commit. Assert work stops by second 115, cleanup completes by second 120, temp files/slots are cleared, pre-dispatch disconnect is cancelled, post-dispatch transport loss is ambiguous, and provider call count is never above one.

- [ ] **Step 3: Implement adapter and orchestration minimally.**

Use streamed httpx request/response APIs. Commit `provider_attempts=1` and dispatch timestamp before HTTP invocation. Perform no retry. Shield privacy cleanup from request cancellation but cap it at the reserved five seconds.

- [ ] **Step 4: Run focused tests and redaction scan.**

Run: `.venv/bin/pytest agent-server/tests/speech_adapters/test_dashscope_asr.py agent-server/tests/speech_core/test_transcription_service.py -q`

Run: `grep -RInE 'transcript|audio_payload|DASHSCOPE_API_KEY' var agent-server/tests/.pytest_cache 2>/dev/null` and verify no runtime artifact contains candidate text/audio/key.

- [ ] **Step 5: Commit Task 4.**

```bash
git add agent-server/focusproof/speech_adapters/dashscope_asr.py agent-server/focusproof/speech_application.py agent-server/tests/speech_adapters/test_dashscope_asr.py agent-server/tests/speech_core/test_transcription_service.py
git commit -m "feat(speech): transcribe through bounded DashScope adapter"
```

### Task 5: ASGI Admission, API, Lifespan Recovery, and BFF Streaming

**Files:**
- Create: `agent-server/focusproof/api/speech_routes.py`
- Create: `agent-server/focusproof/api/speech_models.py`
- Create: `agent-server/focusproof/api/speech_admission.py`
- Modify: `agent-server/focusproof/api/request_limits.py`
- Modify: `agent-server/focusproof/api/app.py`
- Modify: `frontend/app/api/focusproof/[...path]/route.ts`
- Modify: `frontend/lib/api/errors.ts`
- Modify: `frontend/lib/api/proxy-timeout.ts`
- Test: `agent-server/tests/api/test_speech_admission.py`
- Test: `agent-server/tests/api/test_speech_api.py`
- Test: `agent-server/tests/api/test_speech_recovery.py`
- Test: `frontend/tests/api-boundary.test.ts`

**Interfaces:**
- Produces: `POST /sessions/{id}/transcriptions`; ASGI `SpeechAdmissionMiddleware`; lifespan task registry/sweeper; BFF streaming proxy path.
- Consumes: Task 4 service and Task 2 admission token.

- [ ] **Step 1: Write RED tests proving rejected requests read zero multipart bytes.**

Instrument ASGI `receive`. Cover invalid token, non-owner, exhausted quota, invalid/missing UUID key, declared overflow, and chunked overflow. Admission must execute before FastAPI creates `UploadFile`.

- [ ] **Step 2: Write RED API/status/privacy tests.**

Cover all stable errors, capability disabled, one file only, language hint validation, live response shape, no Evidence/EventLog/Review changes, no transcript/audio in DB/logs, disconnect classification, and admission rejection during shutdown.

- [ ] **Step 3: Write RED BFF streaming tests.**

Assert transcription route is allowlisted, multipart body uses duplex stream and never `request.text()`, bearer and idempotency header are forwarded, 11 MiB declared/chunked ceiling holds, timeout is 130 seconds, and upstream JSON remains bounded.

- [ ] **Step 4: Implement middleware, router, lifespan composition/recovery, and BFF changes.**

Store immutable admission token in ASGI scope. Register provider/scanner/controller/service/sweeper/task registry in `app.state`. Startup recovery classifies expired leases and deletes stale UUID-derived temp files; shutdown closes admission and drains/fences work.

- [ ] **Step 5: Run API/BFF gates.**

Run: `.venv/bin/pytest agent-server/tests/api/test_speech_admission.py agent-server/tests/api/test_speech_api.py agent-server/tests/api/test_speech_recovery.py agent-server/tests/api/test_request_limits.py -q`

Run: `cd frontend && npm test -- --run tests/api-boundary.test.ts && npm run typecheck`

- [ ] **Step 6: Commit Task 5.**

```bash
git add agent-server/focusproof/api agent-server/tests/api/test_speech_*.py frontend/app/api/focusproof frontend/lib/api/errors.ts frontend/lib/api/proxy-timeout.ts frontend/tests/api-boundary.test.ts
git commit -m "feat(speech): expose authenticated transcription API"
```

### Task 6: Unified Composer Recorder

**Files:**
- Create: `frontend/features/evidence/SpeechRecorderControl.tsx`
- Create: `frontend/features/evidence/speech-recorder-reducer.ts`
- Modify: `frontend/features/evidence/ImageEvidenceForm.tsx`
- Modify: `frontend/lib/api/client.ts`
- Modify: `frontend/lib/api/contracts.ts`
- Test: `frontend/features/evidence/SpeechRecorderControl.test.tsx`
- Test: `frontend/features/evidence/ImageEvidenceForm.test.tsx`

**Interfaces:**
- Produces: `SpeechRecorderControl` callbacks `onTranscript(text, operationFence)` and `onBusyChange`; API `transcribe(sessionId, file, languageHint, idempotencyKey, signal)`.
- Consumes: Task 5 capability and endpoint; existing controlled evidence textarea/submission.

- [ ] **Step 1: Write reducer RED tests.**

Cover all legal states, illegal stale events, operation/session/composer-revision fencing, timer auto-stop, cancel/unmount cleanup, and no automatic retry.

- [ ] **Step 2: Write component RED tests.**

Mock MediaRecorder/getUserMedia. Cover permission denial, unsupported MIME, tap start/stop, 120-second stop, track cleanup, Blob same-mount retention, explicit retry with new key, late-result suppression, and no extra privacy modal/card.

- [ ] **Step 3: Write unified-composer race tests.**

Assert Submit Evidence is disabled while recording/scanning/transcribing; transcript inserts at captured selection only when composer revision matches; existing text is never overwritten; success does not auto-submit; image/text behavior is unchanged afterward.

- [ ] **Step 4: Implement reducer, control, API method, and composer integration.**

Use Lucide `Mic`, `Square`, and `X` icons with tooltips. Maintain stable layout dimensions and keep all visible voice UI inside the existing composer toolbar/state area.

- [ ] **Step 5: Run frontend gates.**

Run: `cd frontend && npm test -- --run features/evidence/SpeechRecorderControl.test.tsx features/evidence/ImageEvidenceForm.test.tsx && npm run lint && npm run typecheck && npm run build`

- [ ] **Step 6: Commit Task 6.**

```bash
git add frontend/features/evidence frontend/lib/api/client.ts frontend/lib/api/contracts.ts
git commit -m "feat(frontend): add voice-to-text composer input"
```

### Task 7: Browser Journey, Real Clamd, and Real DashScope Acceptance

**Files:**
- Create: `frontend/e2e/speech-evidence.spec.ts`
- Create: `scripts/run_real_speech_gate.py`
- Create: `agent-server/tests/fixtures/real-speech/README.md`
- Modify: `pyproject.toml`
- Modify: `scripts/README.md`

**Interfaces:**
- Produces: deterministic Playwright journey and separately authorized `real_asr` Linux gate.
- Consumes: Tasks 1-6 complete stack.

- [ ] **Step 1: Add deterministic Playwright journey.**

Use fake microphone media and injected fake ASR only in test composition. Verify record -> candidate text -> edit -> existing Submit Evidence -> Evidence list, plus cancel and server error preservation.

- [ ] **Step 2: Add explicit external marker and safe real gate.**

The script must refuse to run without an explicit authorization flag, real provider config, PostgreSQL, real Clamd health, bwrap, and mediainfo. It must use user-provided local Chinese, English, and mixed clips without committing them.

- [ ] **Step 3: Run deterministic E2E.**

Run: `cd frontend && npm run test:e2e -- speech-evidence.spec.ts`

- [ ] **Step 4: Run real acceptance in WSL.**

Run: `.venv/bin/python scripts/run_real_speech_gate.py --authorized`

Expected: all three recordings produce editable candidate text; only manually submitted text becomes Evidence; Clamd clean/malicious/timeout/unavailable/error/oversize matrix passes; no temp audio, transcript persistence, log leakage, process, or container residue remains.

- [ ] **Step 5: Commit Task 7 without credentials or audio.**

```bash
git add frontend/e2e/speech-evidence.spec.ts scripts/run_real_speech_gate.py scripts/README.md agent-server/tests/fixtures/real-speech/README.md pyproject.toml
git commit -m "test(speech): prove deterministic and real transcription flows"
```

### Task 8: Documentation, Full Regression, and Independent Review

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `frontend/README.md`
- Modify: `docs/architecture/`
- Modify: `docs/protocol/`
- Create: `docs/research/AI6_VOICE_TO_TEXT_V1_ACCEPTANCE_REPORT.md`

**Interfaces:**
- Produces: reproducible Linux operation/acceptance evidence and final review packet.
- Consumes: all prior tasks.

- [ ] **Step 1: Document configuration, boundaries, and exclusions.**

Explicitly distinguish review LLM from ASR model, fake from real acceptance, SQLite from PostgreSQL support, native mic permission from external privacy policy, and speech input from Evidence/OpenHands/scoring.

- [ ] **Step 2: Run full backend/frontend regression.**

```bash
cd /home/holy/web3/focusproof-agent
.venv/bin/pytest -m 'not real_llm and not real_asr and not postgres and not staging_external' agent-server/tests -q
.venv/bin/ruff check agent-server scripts
.venv/bin/mypy agent-server/focusproof
cd frontend
npm run lint
npm run typecheck
npm test -- --run
npm run build
npm run test:e2e
```

- [ ] **Step 3: Run migration, PostgreSQL, real security, and real ASR gates.**

Record exact redacted commands/results in the acceptance report. Do not claim production readiness if any external gate is skipped.

- [ ] **Step 4: Verify repository and resource hygiene.**

Run: `git diff --check && git status --short && find . -name '*.orig' -o -name '*.rej'`.

Also prove no service, browser, Docker container, temporary speech file, or test secret remains.

- [ ] **Step 5: Request independent six-axis review.**

Reviewer must not edit code. It checks requirements completeness, logic, edge cases, quality, tests, and actual runtime evidence. Return findings to the implementing task for repair and repeat until the reviewer says the whole V1 is complete with evidence.

- [ ] **Step 6: Commit documentation and final fixes.**

```bash
git add .env.example README.md frontend/README.md docs
git commit -m "docs(speech): close voice-to-text V1 acceptance"
```

## Final Acceptance Checklist

- [ ] User can tap record, stop, receive unchanged text, edit it, and manually submit through the existing text Evidence flow.
- [ ] Raw audio/candidate transcript is absent from DB, EventLog, Evidence, logs, reports, object stores, and Git.
- [ ] Official OpenHands SDK and existing scoring/public Evidence contracts are unchanged.
- [ ] Text/image evidence remains usable when speech is disabled or fails.
- [ ] PostgreSQL races, HMAC rotation, crash recovery, shared image/speech scan slots, shutdown, and cleanup gates pass.
- [ ] Real Clamd and real DashScope Chinese/English/mixed acceptance passes in WSL with redacted evidence.
