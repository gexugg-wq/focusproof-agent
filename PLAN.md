# Plan: FocusProof Voice-to-Text Evidence Input V1
_Locked via grill - by Codex + Holy_

## Goal
Add a replaceable, production-bounded voice input path to FocusProof's existing unified evidence composer. A learner records at most 120 seconds, the backend admits the request before parsing multipart data, scans the temporary audio through the existing malware boundary, inspects one supported audio container, and transcribes it with DashScope `qwen3-asr-flash`. The returned transcript is an unmodified candidate inserted into the existing text composer; only text the learner reviews and explicitly submits becomes Evidence. Raw audio and candidate transcripts never enter Evidence, persistence, OpenHands Conversation/EventLog, scoring, logs, or long-lived storage.

## Approach

1. **Freeze the speech boundary, capability contract, and configuration.**
   - Create `focusproof/speech_core/` for `SpeechTranscriptionProvider`, `AudioInspector`, `TranscriptionService`, value objects, and stable errors. It may depend on the existing `MalwareScanner` port but must not import scoring, Evidence repositories, or `focusproof.openhands_runtime`.
   - Configure `FOCUSPROOF_ASR_PROVIDER=dashscope`, `FOCUSPROOF_ASR_MODEL=qwen3-asr-flash`, `FOCUSPROOF_ASR_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`, `DASHSCOPE_API_KEY`, `FOCUSPROOF_ASR_E2E_TIMEOUT_SECONDS=120`, `FOCUSPROOF_ASR_MAX_CONCURRENCY=4`, and a distinct server HMAC key for idempotency. `DASHSCOPE_MODEL` remains the review LLM setting.
   - Fake ASR is test-injected only. Missing real configuration exposes a disabled capability and never silently selects a fake provider.
   - Publish a versioned discriminated capability in the existing product-capability projection. The enabled variant includes schema version 1, accepted formats, 10 MiB, 120 seconds, `languageHintsAccepted: ["auto", "zh", "en"]`, and `languageHintEffect: "metadata_only"`; the disabled variant carries only a bounded reason code.
   - `languageHint` is accepted metadata for future adapters and has no semantic effect in the pinned DashScope V1 adapter. The provider receives no invented hint parameter. The frontend labels no hint as a recognition guarantee.

2. **Define the durable request state machine before implementing I/O.**
   - Add migration `0008_speech_transcription_requests.py` with two metadata-only tables.
   - `speech_transcription_requests`: UUID `request_id`; FK `session_id`; `owner_user_id`; 64-char HMAC-SHA256 `idempotency_key_hash`; non-empty `hmac_key_version`; nullable 64-char SHA-256 `request_fingerprint`; `state`; `media_type`; `byte_size`; `duration_ms`; `provider`; `model`; checked `provider_attempts` from 0 to 1; `lease_owner`; positive `lease_generation`; `lease_expires_at`; `provider_dispatched_at`; `outcome_code`; `latency_ms`; and timestamps. Add unique `(owner_user_id, session_id, hmac_key_version, idempotency_key_hash)` plus owner/time, session/time, and state/lease indexes. No audio, transcript, upstream body, raw key, filesystem path, or secret column.
   - `speech_resource_slots`: resource-neutral primary key `(resource_kind, slot_number)`; nullable opaque `lease_owner_token`; nullable bounded `work_kind=image|speech`; nullable `work_id`; positive `config_generation`; `enabled`; positive `lease_generation`; `lease_expires_at`. It has no foreign key to a modality-specific request table. Empty slots require all occupancy fields null; occupied slots require all occupancy fields and expiry. Startup reconciliation creates configured `scan` and four `asr` slots. On shrink, surplus free slots are atomically disabled; occupied surplus slots are marked disabled and may only drain, never be reacquired.
   - Exact transitions are `admitted -> uploading -> scanning -> inspecting -> dispatching -> succeeded|failed_terminal|ambiguous`. Pre-dispatch definitive failures also become `failed_terminal`; disconnect before dispatch becomes `cancelled`; after dispatch starts it becomes `ambiguous`. A parsed provider error response, blank transcript, or bounded-response violation is a definitive post-dispatch `failed_terminal`; transport loss/timeout without a definitive response is `ambiguous`. Expired leases become failed-terminal before dispatch and ambiguous after dispatch. Terminal states are succeeded, failed-terminal, cancelled, and ambiguous. V1 has no automatic or same-key provider retry.
   - Every transition is compare-and-swap on `(request_id, lease_generation, lease_owner)`, preventing stale workers from finalizing.
   - Duplicate semantics: the repository hashes a presented key under the active and retained previous HMAC versions. Same key plus different fingerprint is `idempotency_conflict`; active same key is `transcription_in_progress`; succeeded same key is `transcription_result_unavailable`; every terminal failure requires an explicit new key. Readiness fails if a key version referenced by retained rows is unavailable.
   - Database checks enforce the state/payload matrix: active states require lease owner/expiry; terminal states require both lease fields null and completed timestamp; pre-dispatch states require null dispatch timestamp; dispatching/succeeded/ambiguous require dispatch timestamp; failed-terminal permits either null or non-null dispatch timestamp and records whether failure was pre- or post-dispatch in a bounded outcome code; succeeded requires null outcome code and non-null latency; failure/cancel/ambiguous require a bounded outcome code; empty resource slot requires every occupancy field/expiry null; occupied slot requires every occupancy field/expiry and positive generation. Migration tests insert every invalid combination and expect database rejection.
   - Persist in order: provider result received, durable `succeeded` metadata commit, then live transcript response. A crash after commit never causes a second provider call. This is local at-most-once dispatch, not claimed end-to-end exactly once.

3. **Make admission, quotas, concurrency, recovery, and shutdown atomic.**
   - Require a UUID `Idempotency-Key` HTTP header. HMAC-SHA256 it with the dedicated key before persistence/logging.
   - Extend `RequestBodyLimitMiddleware` or add a narrowly scoped ASGI admission middleware for the speech route. After identity resolution but before the first downstream `receive()`, it validates `Idempotency-Key`, verifies session ownership, atomically creates the ledger row, stores an immutable admission token in `scope`, and only then invokes FastAPI multipart parsing. The endpoint consumes that token rather than repeating admission. Tests prove rejected auth/ownership/quota requests make zero downstream body reads.
   - Count every authenticated, owned, newly admitted request against 20 per session lifetime and 30 per owner in a rolling hour, including malformed, malicious, unsupported, timed-out, cancelled, and provider-failed uploads. PostgreSQL always takes the owner advisory lock first and session lock second, with lock/statement timeout bounded by the request deadline, before count/insert. SQLite uses `BEGIN IMMEDIATE`. Multi-worker production requires PostgreSQL.
   - Claim a database-backed `scan` slot before Clamd and `asr` slot before provider dispatch using a server-generated opaque owner token plus modality work reference. Slot waits consume the same deadline; release atomically clears all occupancy fields by generation-fenced update. Request/media deletion never owns slot cleanup semantics.
   - Compose one `MalwareScanner` in FastAPI lifespan and inject the same object into image ingestion and speech transcription. Refactor `compose_media_command()` to accept both the shared scanner and the shared database scan-slot controller. Image and speech must claim the same generation-fenced slot pool before scanning; no path may bypass it.
   - Maintain an application task registry and admission-closed flag. Shutdown rejects new work, waits a bounded grace period, cancels pre-dispatch work, closes provider clients, fences unfinished leases, and deletes temp files. Post-dispatch cancellation is never reclassified as safe retry.
   - Startup and periodic recovery reclaim expired slots, classify expired request leases based on dispatch state, delete orphan speech temp files older than lease grace, and emit bounded recovery counters. Temp names derive from server request UUIDs and are never persisted.

4. **Build a bounded, fail-closed audio pipeline under one deadline.**
   - One 120-second monotonic deadline begins at ASGI request entry. Identity/session lookup, advisory locks, quota/admission, upload, scan, inspection, slot waits, provider dispatch, and bounded response read must stop by second 115. Seconds 115-120 are a shielded privacy-cleanup reserve for terminating inspector/provider resources, closing/deleting temp files, releasing slots, and fencing the ledger even after client cancellation. The BFF timeout is 130 seconds; no phase resets the clock and cleanup never waits on the client connection.
   - Stream exactly one file to a request-scoped regular temp file while computing bytes and SHA-256. Reject above 10 MiB. The middleware limits total multipart bytes, including chunked bodies, to 11 MiB.
   - Scan before parsing. Only `clean` proceeds. Map malicious, oversize, timeout, unavailable, error, and unknown outcomes to fail-closed speech errors. Reuse scanner contracts/audit, not image object storage or quarantine persistence.
   - Inspect using maintained `mediainfo`/`libmediainfo` inside a required production `bubblewrap` sandbox: unshare network, read-only bind only the executable/runtime libraries and one input file, empty tmpfs, no inherited environment/secrets, no shell, 2-second subdeadline, 128 MiB address-space limit, one CPU, 32 file descriptors, 64 KiB output ceiling, and process-group termination. If sandbox prerequisites are unavailable, production capability is disabled. Accept only WebM/Opus, RIFF/WAVE PCM-compatible WAV, and MP3; reject mismatched, malformed, multi-track, zero-duration, and over-120-second input. No FFmpeg, transcoding, AAC/M4A, or hand-rolled parser.
   - Add parser fixtures and malformed/fuzz corpus. The inspector has no network and never logs raw output or temp paths.
   - Poll disconnect during upload and between stages. Cancellation before dispatch becomes cancelled; after durable dispatch it becomes ambiguous. Browser abort is not proof upstream stopped.

5. **Implement the exact DashScope adapter with bounded response handling.**
   - Use the official Beijing OpenAI-compatible `POST /audio/transcriptions` contract with multipart `file` and `model=qwen3-asr-flash`. Parse only bounded `text`, discarding any extra emotion/acoustic fields.
   - Stream-read at most 256 KiB before JSON decoding. Reject non-JSON, oversized, missing/non-string, invalid UTF-8, and blank transcript. Never call unrestricted `.text()` or `.json()` first.
   - Set `provider_attempts=1` and commit `provider_dispatched_at` before invoking HTTP. V1 performs exactly one provider attempt. Any provider transport timeout/disconnect/failure after that boundary is ambiguous; DNS/connect/TLS failures are terminal and still require an explicit new client key. There is no automatic retry or fallback.
   - Stable codes are `audio_too_large`, `audio_too_long`, `unsupported_audio_format`, `invalid_audio`, `transcription_no_speech`, `transcription_timeout`, `transcription_rate_limited`, `transcription_provider_unavailable`, `transcription_ambiguous`, `transcription_result_unavailable`, and `transcription_failed`. No upstream body, URL, key, transcript, or stack trace leaves the adapter.
   - Define only a future Omni analyzer protocol. `qwen3.5-omni-plus` is not configured, invoked, persisted, or scored in V1.

6. **Expose the API and BFF without buffering multipart audio.**
   - Add `POST /sessions/{session_id}/transcriptions` with `Idempotency-Key`, one `file`, and `languageHint=auto|zh|en`. Success returns request ID, transcript, provider, and model only for that live response. No Evidence/EventLog/Agent side effect occurs.
   - Compose repository, shared scanner, inspector, provider, sweeper, registry, and service through lifespan `app.state`; use typed accessors and no module-global fallbacks.
   - Extend the BFF allowlist and multipart predicate. Forward `request.body` as duplex stream, enforce declared/chunked 11 MiB bounds, forward bearer/idempotency headers, use 130 seconds, and retain the 1 MiB bounded JSON response reader. Audio never passes through `request.text()`; a no-stream fallback, if required, has the same hard bound.
   - Add safe frontend mappings for all speech errors. Text/image behavior remains usable if speech is disabled.

7. **Integrate one reducer-driven recorder into the unified composer.**
   - Add one Lucide microphone control to the existing unified composer, extracting a focused child only if needed. Do not add a voice card or second submit action.
   - Use reducer states idle, recording, scanning, transcribing, ready, error. Carry operation generation, session ID, and composer revision in async actions so late results after cancel/unmount/navigation/submission cannot mutate current text.
   - Tap start/stop, timer/cancel, auto-stop at 120 seconds. Choose the first supported MediaRecorder type among WebM/Opus, WAV, MP3. Unsupported browsers lose only the mic; V1 supports desktop Chrome/Edge and Android Chromium, not iOS/Safari.
   - Keep Blob/object URL in current component memory. Retain only for same-mount retry; refresh/navigation loses it. Stop tracks and clear timers, URLs, and Blob after success, cancel/delete, or unmount.
   - Preserve textarea selection with a ref and insert only if operation generation, session, and composer revision all match. Disable the existing Submit Evidence action while recording/scanning/transcribing so submission cannot clear the composer under an in-flight transcript. Never overwrite, rewrite, summarize, auto-submit, or create Evidence.
   - V1 performs no client automatic retry. Any failure preserves current text; same-mount Blob retention supports an explicit new attempt with a new idempotency key. Text/image submission is disabled only while speech is active and otherwise retains existing behavior.
   - Use native microphone permission only. The user rejected extra in-product privacy modal/copy; deployment policy/terms must still disclose cloud transcription outside this component.

8. **Prove contracts with layered gates, including crash/multi-worker behavior.**
   - Unit: transition/constraint matrix, fencing, active/previous HMAC versions and missing-key readiness, provider fixtures, response bounds, sandboxed inspector limits, supported-format boundaries, scanner matrix, one deadline, no-retry classification, disconnect, cleanup.
   - API: auth before multipart read, ownership, UUID key, declared/chunked bounds, status mapping, capability variants, no Evidence/EventLog/Review side effect, redaction, shutdown rejection.
   - Persistence: SQLite plus explicit PostgreSQL marker; migration cycle; atomic quotas; duplicate handling; canonical owner-then-session lock order; two app processes racing; four global slots; config shrink and occupied-slot drain; stale-worker fencing; HMAC rotation; crashes during admission/slot retirement/upload/scan/dispatch/provider response/success commit; restart recovery; schema/content privacy assertions.
   - Security: fake scanner deterministic matrix plus real Clamd clean/malicious/timeout/unavailable/error/oversize. Exercise mixed image/speech contention across independent PostgreSQL app processes and prove both paths share the limit. Provider call count remains zero unless scanner is clean.
   - Frontend/BFF: permission, unsupported browser, timer, cancel, late results, composer-revision race with text/image submission, cursor insertion, multiple recordings, no auto-submit, Blob lifetime, explicit retry, text/image regression, BFF streaming/chunk overflow/timeout/response bound.
   - Playwright uses fake microphone plus injected deterministic ASR. Separately authorized `real_asr` WSL acceptance uses real Clamd, DashScope, and Chinese, English, mixed recordings; proves editable candidate, manual text Evidence, no audio/transcript persistence/logging, no temp residue, and resource cleanup.
   - Emit bounded counters for denial, quota/slot saturation, lease expiry/recovery, scanner/provider latency, definitive post-dispatch failure, ambiguous dispatch, cancellation, cleanup failure, success. Labels contain stable code/provider/model only.

9. **Document and hand off reproducibly.**
   - Update `.env.example`, READMEs, architecture/protocol docs, Linux dependencies/startup (`mediainfo`, Clamd, PostgreSQL), and migration operations.
   - State that voice is optional input, not Evidence or scoring; fake ASR/fake-clean cannot certify production; SQLite is single-process development only.
   - Produce an acceptance report with commands, versions, model, redacted request IDs, language categories, Clamd matrix, crash/restart proof, cleanup, and exclusions. Never include credentials or audio/transcript contents.

## Key decisions & tradeoffs

- **Voice is input, not evidence.** Only learner-confirmed text enters Evidence/OpenHands/scoring.
- **One 120-second backend deadline.** Every phase shares it; BFF permits 130 seconds so backend classification wins.
- **One provider attempt.** V1 has no automatic retry before or after dispatch. Lost success is reported, not replayed or re-billed.
- **Metadata-only recovery.** Leases/fencing/quotas persist; audio/transcript do not, so transcript replay is intentionally impossible.
- **Shared security.** Image and speech use one lifespan-owned scanner plus database-backed cross-worker slots.
- **Maintained inspector, no transcoder.** Isolated `mediainfo` replaces risky handwritten parsing; FFmpeg remains excluded.
- **Specialized ASR now, Omni later.** V1 performs literal transcription only.
- **No extra privacy modal.** This is the user's explicit decision; external policy still discloses cloud processing.
- **Official OpenHands untouched.** No audio content, Agent tool, EventLog, Conversation change, or imitation runtime.

## Risks / open questions

- No product decision remains open. Implementation must verify DashScope entitlement and `mediainfo` behavior on the supported Linux image.
- PostgreSQL is required for production multi-worker quotas/slots; SQLite is single-process development/test only.
- MediaRecorder support is capability-detected; iOS/Safari exclusion is deliberate.
- Cloud transcription disclosure remains a deployment policy obligation despite no component-level modal.

## Out of scope

- Streaming/live captions/WebSocket/press-and-hold.
- iOS/Safari or mini-program client, AAC/M4A, FFmpeg/transcoding.
- Audio Evidence/history/playback/storage or candidate transcript persistence/replay.
- Voiceprint, identity, emotion, accent, fluency, hesitation, background-sound inference.
- Rewrite/correction/summary/automatic submission/Agent invocation/scoring.
- Omni invocation, UI provider choice, provider fallback, object storage/signed URLs/large files.
- Changes to Evidence/scoring, OpenHands SDK/Conversation/EventLog, image verification, or media safety policy.
