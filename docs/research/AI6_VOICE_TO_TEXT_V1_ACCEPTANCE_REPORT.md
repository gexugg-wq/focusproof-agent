# AI6 Voice-to-Text Evidence Input V1 Acceptance Report

Date: 2026-08-31
Repository: `focusproof-agent`
Branch: `ai6-voice-to-text-v1`
Task8 baseline: `ab73fbe23f8f3f9b158f5d09b88cf194c0aaf3a0`

## Decision

Task8 browser-product acceptance passes for the deterministic Chromium journey.
The real provider boundary was accepted during Task7, but the browser-to-real-
provider journey and the external infrastructure gates were not rerun in this
Task8 session. This report therefore does not claim public production readiness.

The inherited Task7 production acceptance used the production DashScope ASR
adapter (`qwen3-asr-flash`), real Clamd, PostgreSQL, and three user-supplied
Chinese, English, and mixed-language recordings. Its acceptance proved the
real provider/scanner boundary, bounded response handling, cleanup, and no
provider retry. The redacted Task7-time observed counters were: provider 3/3
nonblank candidates, Clamd 6/6, PostgreSQL 4/4, privacy 5/5,
`cleanupPassed=true`, and `residueFree=true`. The ephemeral JSON report was
deleted after Task7, and the real provider gate was not rerun during Task8.
The runnable gate is `scripts/run_real_speech_gate.py`; its redacted output
contract is covered by
`agent-server/tests/speech_core/test_real_speech_gate_contract.py`. Task8 adds
the missing product-level browser proof below.

## Product boundary

Voice is optional input to the existing unified Submit Evidence composer. The
browser records at most 120 seconds, uploads one bounded clip, and inserts the
provider transcript unchanged into the existing editable textarea. The learner
can inspect or edit the candidate. Recording never creates Evidence, triggers
review, invokes scoring, or writes OpenHands events. Only the existing Submit
Evidence action submits text through the existing text Evidence path.

Raw audio and candidate text are ephemeral. They are not persisted in Evidence,
the speech metadata ledger, OpenHands EventLog, scoring, reviews, logs, reports,
object storage, or Git. After a retryable API failure, exactly one File remains
in component memory for a user-triggered, same-mount retry of the same clip with
a fresh idempotency key; there is no automatic provider retry and only one
request may be in flight. A non-retryable or unknown failure clears the File and
offers no Retry. Success, cancellation, a new recording, or unmount also clears
that File, media tracks, and any component recording resources. Every failure
preserves the textarea; retry success inserts the raw candidate, but Evidence
remains zero until the learner edits or accepts it and uses the existing Submit
Evidence action. Stale responses remain isolated by the existing fence.

The review LLM settings (`FOCUSPROOF_LLM_*`) are distinct from the ASR settings
(`FOCUSPROOF_ASR_*`, `DASHSCOPE_API_KEY`). Enabled speech requires the real
provider, HMAC key, Clamd, and MediaInfo; production also uses the configured
bubblewrap boundary. SQLite supports single-process development, including
speech. PostgreSQL is required for production multi-worker or cross-process
quota and concurrency enforcement. Fake ASR and fake-clean scanning are
deterministic test doubles only.

## Fresh Task8 evidence

| Area | Command | Result |
|---|---|---|
| Speech/API focused backend | `.venv/bin/pytest agent-server/tests/api/test_speech_admission.py agent-server/tests/api/test_speech_api.py agent-server/tests/api/test_speech_recovery.py agent-server/tests/speech_adapters/test_dashscope_asr.py agent-server/tests/speech_core/test_transcription_service.py -q` | 92 passed; 1 existing deprecation warning |
| Speech/composer focused frontend | `cd frontend && npm test -- --run features/evidence/SpeechRecorderControl.test.tsx features/evidence/speech-recorder-reducer.test.ts tests/unified-evidence-composer.test.tsx tests/api-boundary.test.ts` | 88 passed |
| Full frontend unit suite | `cd frontend && npm test -- --run` | 181 passed |
| Frontend static/build gate | `cd frontend && npm run lint && npm run typecheck && npm run build` | Passed; Next 15.5.21 production build completed |
| Chromium product journey | `cd frontend && npm run test:e2e -- speech-evidence.spec.ts` | 20 passed across Chromium, desktop-1280, mobile, and mobile-360 |
| Backend static gate | `.venv/bin/ruff check agent-server scripts` | Passed |
| Backend strict types | `.venv/bin/mypy agent-server/focusproof` | Passed; 119 source files |

The primary E2E verifies one unified textarea and microphone control; candidate
insertion preserves the raw response; Evidence submission count stays zero
until the learner edits and clicks Submit evidence; exactly one edited text
submission is sent. It also covers permission denial, provider failure while
preserving existing text, non-retryable fail-closed handling, explicit same-clip
retry with a fresh idempotency key, duplicate retry/start clicks, and
cancellation with a late stale response. The four browser projects use a fake
MediaRecorder and mocked transcription route, so no real audio or provider call
is implied by these 20 passes.

## External gates and honest limits

The PostgreSQL service accepted connections during this session, and
`bwrap`, `mediainfo`, and `prlimit` were present. No Clamd socket/service was
available. The marked speech PostgreSQL concurrency command returned 8 skipped
because its explicit PostgreSQL test configuration was not enabled. No real
DashScope call was made in Task8 because no authorized clips, credentials, and
healthy Clamd setup were supplied. The Task7 real boundary acceptance above is
inherited evidence, not a fresh Task8 browser-to-provider run.

The requested deterministic backend regression was also run from the clean
baseline. It reported 1426 passed, 4 skipped, 24 deselected, and 11 failures
before a long-running test ended with KeyboardInterrupt. Those failures were
pre-existing clean-baseline issues outside the Task8 browser journey:
release-artifact fixture redaction/count drift, disabled-media import audit
drift, and legacy FakeUow compatibility with the shared scan-slot refactor.
They were not changed or represented as green in this closeout.

## Reproduction

For deterministic browser proof:

```bash
cd <repository>/frontend
npm run test:e2e -- speech-evidence.spec.ts
```

For the separately authorized real gate, provision fresh PostgreSQL and real
Clamd, provide three absolute local clips, set the speech environment through
the deployment secret manager, and run the command documented in
`scripts/README.md`:

```bash
cd <repository>
.venv/bin/python scripts/run_real_speech_gate.py --authorized \
  --report <redacted-report-target> \
  --chinese <redacted-chinese-clip> \
  --english <redacted-english-clip> \
  --mixed <redacted-mixed-clip>
```

Do not put credentials, candidate text, or audio on the command line or in
the repository. The gate refuses to run without explicit authorization and
does not create Evidence or prove the manual browser Submit Evidence action.

## Six-axis review

1. Requirements: the frozen composer-only, no-auto-submit, explicit same-mount retry, raw-candidate, 120-second, cleanup, and privacy boundaries are represented in implementation and tests.
2. Logic: speech error mapping requires explicit retryability instead of inferring it from HTTP status; reducer fences generation/session/composer revision; retry reuses one retained File with a fresh idempotency key; submission is disabled while speech is active; stale late results are ignored.
3. Edge cases: permission denial, unsupported recorder, stop/cancel, duplicate start/retry, provider failure, existing-text preservation, retry success, unmount, and mobile projects are covered.
4. Quality: focused and full frontend tests, lint, typecheck, build, focused backend tests, Ruff, and strict MyPy passed.
5. Tests: deterministic fake-media/fake-provider coverage passes; real provider/scanner coverage is inherited from Task7 and explicitly separated.
6. Runtime evidence: Task8 proves the browser product loop with fake infrastructure; real Clamd/DashScope browser E2E remains an external follow-up, not an unverified claim.

## Files changed by Task8

- `.env.example`: optional ASR configuration and secret-manager boundary.
- `README.md`: current AI6 scope, privacy boundary, and fake/real distinction.
- `frontend/README.md`: composer and BFF behavior.
- `docs/architecture/ARCHITECTURE.md`: detachable speech architecture and lifecycle boundary.
- `docs/protocol/EVENTS.md`: live transcription API contract without a new Event/Evidence protocol.
- `frontend/features/evidence/SpeechRecorderControl.tsx` and reducer: explicit retained-File retry with lifecycle cleanup and stale-response fencing.
- Frontend unit and E2E tests: retry contract, single-flight behavior, and no-Evidence-before-edit-and-submit proof.
- This report: consolidated Task7 inherited evidence and fresh Task8 results.
