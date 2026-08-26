# AI5.7 Production Media Safety Design

## Purpose
Freeze the production media-safety architecture for FocusProof AI5.7.

## Frozen Rules
- Reuse the official OpenHands SDK 1.31.0 Conversation, EventLog, ActionEvent, ObservationEvent, ToolDefinition, and Tool types directly.
- Do not reimplement or imitate Runtime, Conversation, EventLog, Action, Observation, or Tool.
- Production malware scanning uses `clamd` daemon or sidecar only.
- No CLI scanner, managed scanner, or fake scanner fallback.
- Outcomes are exactly `clean`, `malicious`, `oversize`, `timeout`, `unavailable`, and `error`.
- Persist every scan attempt audit row and every clean receipt.
- Persist definitions freshness and resource bounds on every attempt and receipt:
  - `definitions_version`
  - `definitions_fresh_at`
  - `definitions_age_seconds`
  - `max_bytes`
  - `max_concurrent_scans`
  - `deadline_ms`
  - `socket_timeout_ms`
- Quarantine permissions and TTL must be enforced.
- Image decoding must run in an isolated process.
- Safe facts may only be derived from a clean receipt.
- Real gates must cover benign PNG, EICAR, daemon timeout, daemon unavailable, and daemon error.
- Visual provider must be disabled during the real gate.
- `productionMalwareScanningVerified` remains `false` until every real gate passes.
- Do not modify Manager, Factory, Synchronizer, ResultExtractor, scoring, Monad, or the agent loop.

## Legacy Compatibility Matrix
- `unknown` is migration-only input and must never be emitted by new code.
- `unknown` + daemon missing, socket closed, or sidecar unavailable -> `unavailable` with `daemon_unavailable`.
- `unknown` + deadline exceeded -> `timeout` with `deadline_exceeded`.
- `unknown` + clamd socket, protocol, or worker failure -> `error` with `daemon_error`.
- `unknown` + malware signature hit -> `malicious` with `malware_signature_detected`.
- `unknown` + payload over limit -> `oversize` with `payload_too_large`.
- `unknown` + insufficient or untrusted legacy context -> `error` with `legacy_unknown_unclassified`; never `clean`, never a clean receipt, and never a safe fact.

## Persistence Contract
- `scan_attempts` must persist `attempt_id`, `artifact_sha256`, `content_type`, `scanner_backend`, `definitions_version`, `definitions_fresh_at`, `definitions_age_seconds`, `max_bytes`, `max_concurrent_scans`, `deadline_ms`, `socket_timeout_ms`, `scan_result`, `rejection_code`, `rejection_detail`, `started_at`, `finished_at`, and `idempotency_key`.
- `clean_receipts` must persist `receipt_id`, `attempt_id`, `artifact_sha256`, `receipt_hash`, `scanner_backend`, `definitions_version`, `definitions_fresh_at`, `definitions_age_seconds`, `max_bytes`, `max_concurrent_scans`, `deadline_ms`, `socket_timeout_ms`, `quarantine_path`, `quarantine_expires_at`, and `created_at`.
- The schema must enforce uniqueness for `attempt_id`, `receipt_hash`, and `idempotency_key`.
- Alembic upgrade, downgrade, and re-upgrade must preserve the scan audit tables and constraints.
- Idempotent replay of the same input and idempotency key must create one attempt row and one receipt row, not duplicates.

## Flow
1. Ingestion records a scan attempt.
2. The live `clamd` adapter scans under size, concurrency, deadline, and socket-timeout controls.
3. The result is persisted as one of the frozen outcomes plus a rejection code when the result is not clean.
4. Only a clean receipt may feed quarantine, decoder isolation, and safe facts.
5. The production gate proves the live daemon path and all daemon failure modes.

## Gate Boundary
- `scripts/run_image_evidence_gate.py` is the ordinary guardrail gate; it stays a normal smoke check and does not certify production malware scanning.
- `scripts/run_real_image_evidence_gate.py` is the production certification gate; it must use live `clamd`, keep the visual provider off during execution, and set `productionMalwareScanningVerified` to `false` until all real gate cases pass.
- The real gate is independent of Task 3's `test_media_composition.py` and must not be substituted with that ordinary pytest coverage.

## File Map
- Modify: `agent-server/focusproof/media_core/models.py`
- Modify: `agent-server/focusproof/persistence/models.py`
- Modify: `agent-server/focusproof/persistence/audit_projection.py`
- Modify: `agent-server/focusproof/media_adapters/clamd_malware_scanner.py`
- Modify: `agent-server/focusproof/media_core/ingestion.py`
- Modify: `agent-server/focusproof/bootstrap/media_composition.py`
- Modify: `agent-server/focusproof/config/profiles.py`
- Modify: `agent-server/focusproof/media_adapters/local_quarantine_store.py`
- Modify: `agent-server/focusproof/media_adapters/media_janitor.py`
- Modify: `agent-server/focusproof/media_adapters/pillow_image_codec.py`
- Modify: `agent-server/focusproof/openhands_adapter/events.py`
- Modify: `agent-server/focusproof/openhands_runtime/evidence_messages.py`
- Modify: `agent-server/focusproof/openhands_runtime/media_evidence_facts.py`
- Modify: `agent-server/focusproof/domain/evidence_facts.py`
- Modify: `agent-server/focusproof/runtime/evidence.py`
- Modify: `scripts/run_image_evidence_gate.py`
- Modify: `scripts/run_real_image_evidence_gate.py`
- Modify: `deploy/compose.staging.yml`
- Modify: `deploy/agent-server.Dockerfile`
- Modify: `docs/security/SECURITY_ACCEPTANCE.md`
- Modify: `docs/protocol/EVENTS.md`
- Modify: `agent-server/tests/media_core/test_malware_scanner_contract.py`
- Modify: `agent-server/tests/media_adapters/test_clamd_malware_scanner.py`
- Modify: `agent-server/tests/media_core/test_ingestion.py`
- Modify: `agent-server/tests/media_adapters/test_media_security_policy.py`
- Modify: `agent-server/tests/media_adapters/test_media_composition.py`
- Modify: `agent-server/tests/media_adapters/test_store_janitor_contract.py`
- Modify: `agent-server/tests/media_adapters/test_codec_contract.py`
- Modify: `agent-server/tests/media_adapters/test_recovery_contract.py`
- Modify: `agent-server/tests/openhands_adapter/test_event_projection.py`
- Modify: `agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py`
- Modify: `agent-server/tests/openhands_runtime/test_multimodal_sdk_contracts.py`
- Create: `agent-server/migrations/versions/0006_media_scan_audit_and_receipts.py`
- Create: `agent-server/focusproof/media_adapters/clamd_limits.py`
- Create: `agent-server/focusproof/media_adapters/decoder_worker.py`
- Create: `agent-server/tests/persistence/test_media_scan_audit.py`
- Create: `agent-server/tests/ai5/test_real_media_gate_contract.py`
