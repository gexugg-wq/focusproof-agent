# AI5.3 Production Media Security Design

Status: design approved on 2026-08-14; historical frozen design now
implemented and code-gated. The separate real external clamd clean/EICAR gate
is `BLOCKED_EXTERNAL_SERVICE_GATE` / `REAL_CLAMD_GATE_BLOCKED`.

## Scope and decision

AI5.3 adds mandatory malicious-file admission scanning to the image upload transaction. It scans the exact original quarantined bytes after size/SHA-256 integrity confirmation and before Pillow, normalization, stage, or database finalize. It does not enter the Agent loop, scoring, Monad, frontend, or OpenHands integration.

OpenHands SDK has no malware-scanning responsibility. FocusProof therefore owns this upload-security port. It must not copy or wrap OpenHands Runtime, Conversation, EventLog, Action, Observation, ImageContent, or Tool protocols.

## Authoritative sequence

1. Reserve the media lease.
2. Stream bytes into quarantine while enforcing source size and SHA-256.
3. Finalize/fsync quarantine and compare its size/SHA-256 with streaming facts.
4. Open that opaque, read-only quarantine object and scan it.
5. Proceed only for an explicit `CLEAN` verdict.
6. Validate MIME/container, decode, normalize, and enforce canonical message size.
7. Stage, finalize, mark referenced, and confirm.

The scanner receives a read-only source, never a caller-controlled path. The service retains ownership of stream close and quarantine deletion.

## Core contract

`focusproof.media_core.ports` defines dependency-free frozen models and a protocol:

```python
MalwareScanStatus = Literal[
    "clean", "malicious", "unavailable", "timeout", "error", "unknown"
]

@dataclass(frozen=True, slots=True)
class MalwareScanVerdict:
    status: MalwareScanStatus
    engine: str
    signature_version: str | None = None

class MalwareScanner(Protocol):
    def scan(self, source: ReadOnlyMediaSource) -> MalwareScanVerdict: ...
```

Core must not import clamd, sockets, subprocesses, Pillow, FastAPI, persistence, or OpenHands. Blank engines and unknown status values are invalid. Verdicts contain no payload, path, quarantine ID, filename, ClamAV signature name, endpoint, raw response, credentials, or exception text.

Stable rejection codes are `media_malware_detected`, `media_scan_unavailable`, `media_scan_timeout`, `media_scan_failed`, and `media_scan_unknown`. Scanner exceptions map to failed without exposing their cause. Each non-clean status maps one-to-one to the corresponding code and retains no raw adapter text.

## Profile and composition policy

- `staging` and `production`: scanner mode must be `clamd`; missing, disabled, fake, or invalid settings fail application/media composition at startup. There is no fallback, no endpoint registration, and no fake-clean scanner path.
- `local-dev`: media upload requires an explicit `clamd`, deterministic fake, or `disabled` mode. `disabled` means the entire media upload capability is off; the service must not skip scanning and then accept uploads, and it must not build a fake-clean scanner or enter quarantine/decode/stage/finalize. The endpoint is either not registered or returns stable `media_disabled`.
- `deterministic-test`: tests explicitly inject clean, malicious, unknown, timeout, or raising fakes. `disabled` is not a fake-clean substitute; it only disables upload capability when explicitly selected. Production composition can never select a fake.

A frozen `MediaSecurityPolicy` owns mode, endpoint, connect timeout, total/admission timeout, maximum scan bytes, and maximum concurrent scans. Scanner configuration is independent of LLM vision configuration. `bootstrap/media_composition.py` selects and injects the scanner into `MediaIngestionService`; it never registers it as an OpenHands tool or verification capability.

## ClamAV/clamd adapter

The production adapter uses the documented clamd streaming protocol over an explicit Unix socket or TCP endpoint. It must:

- stream the already-verified quarantine handle and never reopen a supplied path;
- enforce maximum bytes before and during transfer; configured scan capacity must be at least the authoritative 10 MiB source limit, and truncation/daemon size-limit responses are never clean;
- bound connection, queue/admission, and total scan time;
- bound concurrency with a semaphore;
- close socket/stream and release capacity exactly once on success, error, timeout, or cancellation;
- map only explicit `OK` to `CLEAN`, `FOUND` to `MALICIOUS`, and every error, unknown, empty, multiple, malformed, EOF, timeout, refusal, or protocol exception to unavailable/unknown;
- perform no hidden retry within one upload.

The adapter must not shell out with user-controlled input. Any new Python dependency must be pinned and included in build/import verification.

## TOCTOU and cancellation

The scan is bound to the finalized quarantine payload whose byte size and SHA-256 were verified. The store rejects symlinks and non-regular objects and must not replace a finalized payload. Decode reopens the same opaque object, and the existing codec source-fact verification remains mandatory. Scanner decisions are not cached by filename or MIME. Digest-result caching is outside AI5.3.

Cancellation while queued or scanning propagates after cleanup; it is never converted to clean or a generic success. Admission capacity, sockets, and streams are released exactly once.

## Failure atomicity

Every outcome except explicit `CLEAN` occurs before `object_store.stage()` and transaction `finalize()`. Malicious, unavailable, timeout, unknown, raised, or cancelled scans must close the scan stream, delete/close quarantine, reject the active lease with a stable internal category, report secondary cleanup failures without replacing the primary failure, and perform no stage/finalize/reference action. If a normalized source somehow exists, it is closed.

## Public errors and observability

Public responses expose only:

- `media_malicious`: HTTP 422, non-retryable;
- `media_scan_unavailable`: HTTP 503, retryable;
- existing `media_ingestion_failed`: generic fallback.

Responses and logs never disclose filenames, paths, object keys, endpoints, source bytes, clamd raw responses, signatures, or nested exception text. Low-cardinality metrics distinguish clean, malicious, unavailable, timeout, unknown, cancellation, queue rejection, and duration.

## Mandatory TDD matrix

| Case | Required proof |
|---|---|
| clean PNG | scanner runs before validator; ingestion succeeds |
| EICAR | malicious; no decode/stage/finalize; cleanup |
| unavailable | retryable stable error; cleanup |
| timeout | retryable stable error; cleanup |
| unknown/malformed | fail closed; cleanup |
| adapter raises | fail closed; raw text absent |
| exact/over maximum bytes | exact accepted; over rejected before transfer/decode |
| staging/production fake or disabled | startup/composition failure, no endpoint registration |
| local/test disabled upload | rejected or `media_disabled`, zero side effects |
| local explicit fake | deterministic behavior only when selected, never as a disabled fallback |
| concurrent scans | active count never exceeds configured limit |
| queued/in-flight cancellation | propagation, close, capacity release, cleanup |

Deterministic adapter tests use a controlled fake clamd protocol server. A guarded real-clamd test may run in staging but must not be required by default tests.

## Allowed files

Implementation may modify `media_core/ports.py`, `media_core/ingestion.py`, `media_application.py`, `bootstrap/media_composition.py`, `config/profiles.py`, `api/media_routes.py`; create focused clamd/fake adapters; update dependency/build configuration only if needed; and add corresponding core, adapter, config, API, integration, and architecture tests plus deployment documentation.

## Protected files and duties

Do not modify or introduce scanner decisions/imports into `focusproof/openhands_runtime/**`, `focusproof/openhands_adapter/**`, scoring, `focusproof/domain/plugins/**`, Monad, `frontend/**`, or OpenHands persistence/protocol surfaces. Architecture tests must enforce these exclusions.

## Migration and rollout

No database migration is required; scan verdicts are admission decisions, not evidence metadata. Rollout: deploy and health-check clamd, configure scanner with upload disabled in environments that intentionally expose no upload capability, run deterministic and guarded staging gates, enable staging upload, observe bounded metrics, then seek separate production authorization. Rollback disables public upload; staging/production must never fall back to unscanned upload, fake-clean acceptance, or disabled-as-bypass behavior.

## Remaining risks

EventLog data-URL retention, janitor scheduling, natural-image semantic validation, and document/audio scanning remain separate and are not solved by AI5.3.
