# AI5.7 Production Media Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Ship a production-only media safety pipeline that uses live `clamd` scanning, persists audit evidence, fails closed when scanning is not proven, and only emits safe facts from clean receipts.

**Architecture:** Split the work across contracts, scanner migration, ingestion, quarantine, safe-fact projection, and deployment gates. Each task has a narrow file set, a red test, a minimal green change, and an AI0 approval checkpoint.

**Tech Stack:** Python 3.12, Alembic, pytest, the official OpenHands SDK 1.31.0 event and tool types, `clamd`, and the existing FocusProof agent-server package.

**Spec:** `docs/superpowers/specs/2026-08-20-ai5-7-production-media-safety-design.md`

## Global Constraints
- Reuse the official OpenHands SDK 1.31.0 Conversation, EventLog, ActionEvent, ObservationEvent, ToolDefinition, and Tool types directly.
- Do not reimplement Runtime, Conversation, EventLog, Action, Observation, or Tool.
- Production malware scanning uses `clamd` daemon or sidecar only.
- No CLI scanner, managed scanner, or fake scanner fallback.
- Outcomes are exactly `clean`, `malicious`, `oversize`, `timeout`, `unavailable`, and `error`.
- `unknown` is migration-only input from legacy rows; live code must never emit it.
- Persist every scan attempt audit row and every clean receipt.
- Persist definitions freshness and resource bounds snapshots on each attempt and receipt.
- Quarantine permissions and TTL must be enforced.
- Image decoding must run in an isolated process.
- Safe facts may only be derived from a clean receipt.
- The ordinary gate and the real gate are different boundaries; only the real gate certifies production malware scanning.
- `productionMalwareScanningVerified` remains `false` until every real gate case passes.
- Do not modify Manager, Factory, Synchronizer, ResultExtractor, scoring, Monad, or the agent loop.

## Legacy Outcome Compatibility
- Legacy `unknown` replay rows must be normalized to a frozen outcome using the stored rejection code.
- `unknown` with daemon-unavailable context becomes `unavailable`.
- `unknown` with deadline-exceeded context becomes `timeout`.
- `unknown` with socket, protocol, or worker failure becomes `error`.
- `unknown` with malware signature context becomes `malicious`.
- `unknown` with payload-over-limit context becomes `oversize`.
- Any live-path result that is not `clean` must carry a rejection code and must not produce a clean receipt.

---

### Task 1: Contracts, Models, and Audit Persistence

**Files:**
- Modify: `agent-server/focusproof/media_core/models.py`
- Modify: `agent-server/focusproof/persistence/models.py`
- Modify: `agent-server/focusproof/persistence/audit_projection.py`
- Modify: `agent-server/tests/media_core/test_malware_scanner_contract.py`
- Create: `agent-server/migrations/versions/0006_media_scan_audit_and_receipts.py`
- Create: `agent-server/tests/persistence/test_media_scan_audit.py`

**Interfaces:**
- Consumes: `MediaScanAttempt`, `MediaCleanReceipt`, `ScanResultKind`, and `ScanRejectionCode`.
- Produces: audit rows, receipt rows, unique constraints, and replay-safe persistence that downstream tasks can trust.

- [ ] **RED: define exact frozen outcomes and persistence fields**

```python
def test_scan_result_kind_has_exact_members():
    assert {item.value for item in ScanResultKind} == {
        "clean",
        "malicious",
        "oversize",
        "timeout",
        "unavailable",
        "error",
    }
```

Run: `./.venv/bin/python -m pytest agent-server/tests/media_core/test_malware_scanner_contract.py -q`

Expected: FAIL because the current contract still exposes the legacy `unknown` path or lacks the frozen persistence fields.

- [ ] **RED: verify Alembic upgrade, downgrade, and re-upgrade plus idempotent replay**

```python
def test_media_scan_audit_migration_replays_cleanly(alembic_runner, scan_service):
    alembic_runner.upgrade("head")
    alembic_runner.downgrade("base")
    alembic_runner.upgrade("head")
    first = scan_service.scan_bytes(b"png-bytes", idempotency_key="same-key")
    second = scan_service.scan_bytes(b"png-bytes", idempotency_key="same-key")
    assert first.attempt_id == second.attempt_id
```

Run: `./.venv/bin/python -m pytest agent-server/tests/persistence/test_media_scan_audit.py -q`

Expected: FAIL because the new migration, unique constraints, or replay semantics are not yet present.

- [ ] **GREEN: add the audit tables, unique constraints, and replay-safe repository**

Implement `scan_attempts` and `clean_receipts` with the frozen freshness and resource-bound snapshots, add the unique constraints for `attempt_id`, `receipt_hash`, and `idempotency_key`, and make the migration downgrade/re-upgrade without losing the contract.

- [ ] **PASS**

Run: `./.venv/bin/python -m pytest agent-server/tests/media_core/test_malware_scanner_contract.py agent-server/tests/persistence/test_media_scan_audit.py -q`

Expected: PASS.

- [ ] **AI0 review stop**

Stop here and hand the diff to AI0 before changing the scanner adapter.

### Task 2: Clamd Adapter and Resource Control Migration

**Files:**
- Modify: `agent-server/focusproof/media_adapters/clamd_malware_scanner.py`
- Modify: `agent-server/focusproof/media_core/ports.py`
- Modify: `agent-server/focusproof/media_core/limits.py`
- Modify: `agent-server/tests/media_adapters/test_clamd_malware_scanner.py`
- Create: `agent-server/focusproof/media_adapters/clamd_limits.py`

**Interfaces:**
- Consumes: the frozen enum and persistence fields from Task 1.
- Produces: a migrated daemon-backed scanner that maps the old implementation to the frozen contract, while preserving live daemon-only behavior and explicit rejection codes.

- [ ] **RED: prove the legacy adapter still leaks `unknown` or loses a rejection code**

```python
@pytest.mark.parametrize(
    "fixture_name,expected_result,expected_code",
    [
        ("benign_png", "clean", None),
        ("eicar", "malicious", "malware_signature_detected"),
        ("oversize", "oversize", "payload_too_large"),
        ("timeout", "timeout", "deadline_exceeded"),
        ("unavailable", "unavailable", "daemon_unavailable"),
        ("error", "error", "daemon_error"),
    ],
)
def test_clamd_scanner_maps_exact_outcomes(fixture_name, expected_result, expected_code):
    result = scan_fixture(fixture_name)
    assert result.scan_result == expected_result
    assert result.rejection_code == expected_code
```

Run: `./.venv/bin/python -m pytest agent-server/tests/media_adapters/test_clamd_malware_scanner.py -q`

Expected: FAIL because the current scanner still returns legacy `unknown` behavior or incomplete rejection codes for at least one case.

- [ ] **RED: verify timeout, error, and cancel release semaphore, socket, and worker state**

```python
def test_clamd_scanner_releases_resources_on_timeout_error_and_cancel(resource_probe):
    for outcome in ("timeout", "error", "cancel"):
        probe = resource_probe()
        run_scan_with_outcome(outcome, probe)
        assert probe.semaphore_released is True
        assert probe.socket_closed is True
        assert probe.worker_state_cleared is True
```

Run: `./.venv/bin/python -m pytest agent-server/tests/media_adapters/test_clamd_malware_scanner.py -q`

Expected: FAIL because the current adapter still leaks a semaphore, socket, or worker path on at least one failure mode.

- [ ] **GREEN: migrate the existing scanner in place**

Keep the existing daemon-backed path, move its size/concurrency/deadline knobs into `clamd_limits.py`, translate live daemon responses into the exact frozen enum, and guarantee cleanup in `finally` for timeout, error, and cancel paths.

- [ ] **PASS**

Run: `./.venv/bin/python -m pytest agent-server/tests/media_adapters/test_clamd_malware_scanner.py -q`

Expected: PASS.

- [ ] **AI0 review stop**

Stop here before editing ingestion or profile composition.

### Task 3: Ingestion, Composition, and Fail-Closed Profiles

**Files:**
- Modify: `agent-server/focusproof/media_core/ingestion.py`
- Modify: `agent-server/focusproof/bootstrap/media_composition.py`
- Modify: `agent-server/focusproof/config/profiles.py`
- Modify: `agent-server/tests/media_core/test_ingestion.py`
- Modify: `agent-server/tests/media_adapters/test_media_security_policy.py`
- Modify: `agent-server/tests/media_adapters/test_media_composition.py`

**Interfaces:**
- Consumes: the migrated scanner, the clean receipt contract, and the production profile flags.
- Produces: fail-closed ingestion and production wiring that refuses anything except a clean receipt.

- [ ] **RED: prove ingestion rejects non-clean scan results**

```python
@pytest.mark.parametrize("scan_result", ["malicious", "oversize", "timeout", "unavailable", "error"])
def test_ingestion_blocks_non_clean_results(scan_result):
    with pytest.raises(MediaScanBlockedError):
        ingest_media(payload=b"file-bytes", profile="production", scan_result=scan_result)
```

Run: `./.venv/bin/python -m pytest agent-server/tests/media_core/test_ingestion.py -q`

Expected: FAIL because the current flow still lets at least one non-clean result reach downstream composition.

- [ ] **RED: prove production profile keeps the visual provider off until the real gate passes**

```python
def test_production_profile_disables_visual_provider_until_real_gate_passes():
    profile = load_profile("production")
    assert profile.visual_provider_enabled is False
```

Run: `./.venv/bin/python -m pytest agent-server/tests/media_adapters/test_media_security_policy.py -q`

Expected: FAIL if any production composition path still enables the visual provider before the real gate is certified.

- [ ] **GREEN: make ingestion fail closed**

Require a clean receipt before downstream flow continues, keep the visual provider disabled in production, and make the composition path resolve the migrated live scanner only.

- [ ] **PASS**

Run: `./.venv/bin/python -m pytest agent-server/tests/media_core/test_ingestion.py agent-server/tests/media_adapters/test_media_security_policy.py agent-server/tests/media_adapters/test_media_composition.py -q`

Expected: PASS.

- [ ] **AI0 review stop**

Stop here before touching quarantine or decoder isolation.

### Task 4: Quarantine, Janitor, and Decoder Process Isolation

**Files:**
- Modify: `agent-server/focusproof/media_adapters/local_quarantine_store.py`
- Modify: `agent-server/focusproof/media_adapters/media_janitor.py`
- Modify: `agent-server/focusproof/media_adapters/pillow_image_codec.py`
- Modify: `agent-server/tests/media_adapters/test_store_janitor_contract.py`
- Modify: `agent-server/tests/media_adapters/test_codec_contract.py`
- Modify: `agent-server/tests/media_adapters/test_recovery_contract.py`
- Create: `agent-server/focusproof/media_adapters/decoder_worker.py`

**Interfaces:**
- Consumes: clean receipts from Tasks 1-3 and the quarantine metadata contract.
- Produces: TTL-bound quarantine storage, janitor cleanup rules, and an isolated decoder worker that never runs in the main process.

- [ ] **RED: prove quarantine TTL and permissions are still not enforced**

```python
def test_quarantine_entry_expires_after_ttl_and_is_removed():
    store = build_quarantine_store(ttl_seconds=60)
    assert store.is_expired(created_at=now_minus_61_seconds()) is True
```

Run: `./.venv/bin/python -m pytest agent-server/tests/media_adapters/test_store_janitor_contract.py -q`

Expected: FAIL because the current store and janitor still leave at least one expired or unauthorized artifact behind.

- [ ] **RED: prove decoder isolation and cleanup after timeout, error, and cancel**

```python
@pytest.mark.parametrize("outcome", ["timeout", "error", "cancel"])
def test_image_decoder_runs_in_separate_process_and_cleans_tempfiles(outcome):
    result = run_decoder(outcome)
    assert result.worker_pid != os.getpid()
    assert result.tempfiles_removed is True
    assert result.quarantine_released is True
```

Run: `./.venv/bin/python -m pytest agent-server/tests/media_adapters/test_codec_contract.py agent-server/tests/media_adapters/test_recovery_contract.py -q`

Expected: FAIL because the current decoder still runs in-process or leaves worker/temp/quarantine state behind on at least one failure mode.

- [ ] **GREEN: add quarantine TTL cleanup and process-isolated decoding**

Implement the TTL cleanup, permission checks, and `decoder_worker.py`, and make every timeout, error, or cancel path release the worker, temp file, and quarantine state.

- [ ] **PASS**

Run: `./.venv/bin/python -m pytest agent-server/tests/media_adapters/test_store_janitor_contract.py agent-server/tests/media_adapters/test_codec_contract.py agent-server/tests/media_adapters/test_recovery_contract.py -q`

Expected: PASS.

- [ ] **AI0 review stop**

Stop here before changing safe-fact projection or official OpenHands event emission.

### Task 5: Safe Facts and Official OpenHands Events

**Files:**
- Modify: `agent-server/focusproof/domain/evidence_facts.py`
- Modify: `agent-server/focusproof/openhands_adapter/events.py`
- Modify: `agent-server/focusproof/openhands_runtime/evidence_messages.py`
- Modify: `agent-server/focusproof/openhands_runtime/media_evidence_facts.py`
- Modify: `agent-server/focusproof/runtime/evidence.py`
- Modify: `agent-server/tests/openhands_adapter/test_event_projection.py`
- Modify: `agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py`
- Modify: `agent-server/tests/openhands_runtime/test_multimodal_sdk_contracts.py`

**Interfaces:**
- Consumes: actual clean receipts only. Legacy replay rows with `unknown` are migration inputs that must be normalized to a frozen non-clean outcome or `error` before any projection step; they never produce a clean receipt.
- Produces: safe facts and official OpenHands event objects, with a hard rejection point for any non-clean receipt before projection.

- [ ] **RED: prove legacy `unknown` replay never becomes clean**

```python
def test_legacy_unknown_daemon_unavailable_maps_to_unavailable_without_clean_receipt():
    replay = load_legacy_replay_row(verdict="unknown", rejection_code="daemon_unavailable")
    result = reconcile_legacy_replay(replay)
    assert result.scan_result == "unavailable"
    assert result.source_receipt_kind != "clean"
    assert result.clean_receipt_id is None
```

Run: `./.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py -q`

Expected: FAIL because the current projection still treats a legacy `unknown` replay as if it could become a clean receipt.

- [ ] **RED: prove replay/reconcile stays idempotent and emits no safe facts for non-clean legacy input**

```python
def test_legacy_unknown_replay_is_idempotent_and_emits_no_safe_facts():
    replay = load_legacy_replay_row(verdict="unknown", rejection_code="daemon_unavailable")
    first = reconcile_legacy_replay(replay)
    second = reconcile_legacy_replay(replay)
    assert first.attempt_id == second.attempt_id
    assert first.scan_result == second.scan_result
    assert first.safe_fact_count == 0
```

Run: `./.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py -q`

Expected: FAIL because the current migration path still permits a legacy replay row to cross into safe-fact projection or lacks replay idempotency.

- [ ] **RED: prove non-clean receipts are rejected before official OpenHands emission**

```python
def test_openhands_projection_rejects_non_clean_receipts_before_emitting_events():
    with pytest.raises(UnsafeFactError):
        project_media_fact(receipt={"scan_result": "malicious"})
```

Run: `./.venv/bin/python -m pytest agent-server/tests/openhands_adapter/test_event_projection.py agent-server/tests/openhands_runtime/test_multimodal_sdk_contracts.py -q`

Expected: FAIL because the current projection still allows a non-clean receipt to reach the official OpenHands event layer or uses a legacy fallback type.

- [ ] **GREEN: keep the migration boundary narrow**

Normalize legacy replay rows to frozen non-clean outcomes or `error` using stored rejection codes, never fabricate a clean receipt from `unknown`, reject all non-clean receipts in the safe-fact layer, and emit only official OpenHands Conversation/EventLog/ActionEvent/ObservationEvent/ToolDefinition/Tool objects from actual clean receipts.

- [ ] **PASS**

Run: `./.venv/bin/python -m pytest agent-server/tests/openhands_adapter/test_event_projection.py agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py agent-server/tests/openhands_runtime/test_multimodal_sdk_contracts.py -q`

Expected: PASS.

- [ ] **AI0 review stop**

Stop here before deployment wiring or real gate certification.

### Task 6: Deployment, Real Clamd Gate, and Docs

**Files:**
- Modify: `deploy/compose.staging.yml`
- Modify: `deploy/agent-server.Dockerfile`
- Modify: `scripts/run_real_image_evidence_gate.py`
- Modify: `scripts/run_image_evidence_gate.py`
- Modify: `docs/security/SECURITY_ACCEPTANCE.md`
- Modify: `docs/protocol/EVENTS.md`
- Create: `agent-server/tests/ai5/test_real_media_gate_contract.py`

**Interfaces:**
- Consumes: the production-only media pipeline and the safe-fact projection path from Tasks 1-5.
- Produces: a live-daemon deployment gate that is independent of the ordinary gate and certifies production malware scanning only after all real cases pass.

- [ ] **RED: prove the real gate is independent from ordinary pytest composition checks**

```python
def test_real_media_gate_requires_live_clamd_and_visual_provider_off():
    result = run_real_media_gate()
    assert result.uses_live_clamd is True
    assert result.visual_provider_enabled is False
    assert result.production_malware_scanning_verified is False
```

Run: `./.venv/bin/python -m pytest agent-server/tests/ai5/test_real_media_gate_contract.py -q`

Expected: FAIL because the current real gate is not yet isolated from the ordinary gate or still depends on the pre-existing composition test path.

- [ ] **RED: prove the real gate covers the full live-daemon matrix**

```python
@pytest.mark.parametrize("case_name", ["benign_png", "eicar", "timeout", "unavailable", "error"])
def test_real_media_gate_covers_every_required_daemon_case(case_name):
    outcome = run_real_media_gate_case(case_name)
    assert outcome.passed is True
```

Run: `./.venv/bin/python scripts/run_real_image_evidence_gate.py`

Expected: FAIL because at least one of benign PNG, EICAR, timeout, unavailable, or error is not yet proven against live `clamd`.

- [ ] **GREEN: wire the deployment and gate to the live daemon only**

Keep `scripts/run_image_evidence_gate.py` as the ordinary guardrail, keep `scripts/run_real_image_evidence_gate.py` as the production certification gate, hold the visual provider off during the real gate, and leave `productionMalwareScanningVerified` false until the full matrix passes.

- [ ] **PASS**

Run: `./.venv/bin/python scripts/run_real_image_evidence_gate.py`

Expected: PASS only after the live daemon proves benign PNG, EICAR, timeout, unavailable, and error, with the visual provider disabled during the entire run.

- [ ] **AI0 review stop**

Stop here before any commit or deployment handoff.
