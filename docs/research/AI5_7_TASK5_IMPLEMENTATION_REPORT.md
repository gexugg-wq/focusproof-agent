# AI5.7 Task5 Implementation Report

Status: AI5_7_TASK5_IMPLEMENTATION_READY
Date: 2026-08-24
Executor: AI5.7 Task5 OpenHands multimodal event integration

## Adopted specification boundary

Primary Task5 authority:
- docs/superpowers/plans/2026-08-20-ai5-7-production-media-safety.md

Read as precondition/history, not as permission to re-enter earlier tasks:
- docs/superpowers/plans/2026-08-12-ai5-multimodal-image-foundation.md
- docs/superpowers/plans/2026-08-14-ai5-3-production-media-security.md
- docs/architecture/OPENHANDS_REUSE_STRATEGY.md
- docs/architecture/ARCHITECTURE.md
- docs/protocol/EVENTS.md
- docs/project-management/goals/AI5_MULTIMODAL_IMAGE_FOUNDATION_CODEX_GOAL.md
- docs/project-management/TASK_BOARD.md
- docs/research/AI5_7_TASK1_IMPLEMENTATION_REPORT.md
- docs/research/AI5_7_TASK2_IMPLEMENTATION_REPORT.md
- docs/research/AI5_7_TASK3_IMPLEMENTATION_REPORT.md
- docs/research/AI5_7_TASK4_IMPLEMENTATION_REPORT.md
- docs/research/AI5_IMAGE_GATE_REPORT.md

Boundary enforced: Task5 only projects already accepted clean image evidence into official OpenHands Conversation/EventLog safe facts. This work does not connect a real visual provider, live ClamAV production scan, production deployment, Monad logic, or real LLM execution.

## Root cause and traced data flow

Current chain before Task5 was:
receipt / scan attempt / scan result -> persistence provider -> MediaEvidenceFacts -> evidence message -> LocalConversation/EventLog.

The unsafe gap was that message facts could be derived from a referenced media artifact and mutable evidence metadata without requiring a persisted active clean receipt. In addition, the legacy adapter path still had FocusProof raw-dict projection helpers instead of official SDK MessageEvent objects. Dimensions for safe facts were initially read from evidence.metadata_json, which could be edited after upload; this was corrected to use the completed media reservation attributes snapshot.

Custom/raw projection locations confirmed:
- focusproof.openhands_adapter.events returned raw dict payloads before this task.
- focusproof.runtime.events.Event remains the product audit event model, but Task5 OpenHands projection now converts it to official MessageEvent rather than projecting raw dicts.
- focusproof.openhands_runtime.synchronizer already persists official LocalConversation messages; Task5 keeps that official path and hardens the image safe-fact source.

## Implementation summary

Clean receipt authority:
- MediaMessageArtifactFacts and MediaEvidenceFacts now require receipt_id, attempt_id, scan_result, artifact/evidence id, media type, bounded size/dimensions, artifact_sha256, normalized_sha256, and learner explanation where applicable.
- SqlEvidenceRepository.get_media_message_artifact now joins the session owner, evidence, media artifact, completed media reservation, scan attempt, and active clean receipt. Missing/non-clean/pending/unavailable/error relations fail closed before any OpenHands message contribution.
- ScopedSessionEvidenceRepository.get_media_evidence_facts now uses provider.get_facts, never reads image bytes for safe-fact projection, and maps unavailable clean facts to MediaEvidenceNotReady.
- RuntimeEvidenceMessageFactory emits only stable safe fields and refuses non-clean facts.

Legacy migration:
- normalize_legacy_scan_projection is deterministic and replay-safe.
- legacy unknown or missing scan_result never becomes clean.
- known rejection codes normalize to non-clean/error categories with safe_fact_count=0 unless an explicit clean receipt id exists.

Official OpenHands projection:
- focusproof_event_to_openhands_message returns openhands.sdk.event.MessageEvent with openhands.sdk.llm.Message/TextContent.
- sender/source identity comes from the verified product Event actor mapping, not from payload self-reporting.
- raw dict payloads are rejected by openhands_message_to_focusproof_payload.
- message_key remains stable via the existing envelope path; repeated sync/replay produces one equivalent contribution per clean receipt/evidence.

Safe event field example:
- receipt_id
- attempt_id
- scan_result = clean
- artifact_ref
- artifact_sha256
- media_type
- normalized_sha256
- byte_size
- width
- height

Fields intentionally absent from Agent-visible facts:
- image bytes
- base64/data URLs
- temporary paths
- object/opaque keys
- quarantine paths
- credentials/tokens
- full private metadata
- payload-forged sender

## Repository recovery incident

During implementation, a block replacement in agent-server/focusproof/persistence/repositories.py initially targeted the EvidenceRepository protocol area instead of only the intended SQL repository area. This caused missing repository protocol/SQL definitions and duplicate SqlAuditEventRepository during repair.

Recovery method:
- Copied the current broken file to /tmp/ai5_7_task5_repositories_broken_audit.py for audit before further edits.
- Did not use git checkout/reset/revert/clean and did not stage/commit.
- Used HEAD only as read-only skeleton reference.
- Compared AST class/method inventories for current vs HEAD.
- Restored missing protocol and SQL repository definitions while preserving Task4 additions already present in the current file, including SqlMediaTransactionRepository, media scan/receipt imports, media models, and UoW-facing APIs.
- Removed the duplicated SqlAuditEventRepository block.
- Verified no duplicate classes remained and repositories.py py_compile passed.

Why prior changes were not lost:
- The file was not overwritten from HEAD.
- The restored section was rebuilt around the current file content and retained Task4 media transaction classes, media receipt models, imports, and existing tests/callers.
- Recovery proof tests below covered repository/provider/API/Task4 paths before returning to Task5 GREEN.

## RED and GREEN evidence

RED evidence:
- Task5 focused RED initially failed as expected: 5 failed, 19 passed.
- Failures covered raw dict projection, missing official MessageEvent, non-clean/missing clean receipt still contributing, and missing receipt_id/attempt_id/scan_result safe fields.
- Added legacy unknown normalization RED, which initially failed by missing normalize_legacy_scan_projection import.

Focused GREEN:
- Task5 focused rerun: 30 passed in 8.35s.
- OpenHands adapter/runtime contracts excluding real LLM: 266 passed, 1 warning in 15.11s.

Recovery proof:
- py_compile repositories.py: passed.
- import smoke for repositories/providers/unit_of_work/api app: passed.
- repository/provider/API/architecture recovery suite: 135 passed, 1 warning in 27.80s.
- Task4 per-file timeout audit:
  - test_ingestion.py with 180s file timeout: timed out at file level, no assertion failure.
  - test_real_sqlite_claim_publication_window_has_one_retryable_then_converges alone: 1 passed in 56.96s.
  - test_ingestion.py with approved 300s historical-slow timeout: 68 passed in 271.51s.
  - test_crash_states.py: 1 passed.
  - test_malware_scanner_contract.py: 64 passed.
  - test_task3_adapters.py: 6 passed.
  - test_recovery_contract.py: 39 passed.
  - test_media_security_policy.py: 40 passed.
  - test_codec_contract.py: 44 passed.
  - test_build_import_contract.py: 6 passed.
  - test_media_malware_admission.py: 7 passed, 1 skipped.

Task1-4 regression:
- Remaining AI5/API/domain/composition/store/postgres-gated regression: 393 passed, 5 skipped, 1 deselected, 1 warning in 56.80s.

Full backend default, non-external/non-real LLM:
- Command excluded agent-server/tests/openhands_runtime/test_real_llm.py and used the repository pytest defaults for not real_llm/postgres/staging_external.
- Final rerun after the last type-only fix: 1855 passed, 6 skipped, 14 deselected, 470 warnings in 719.02s.

Hygiene:
- Ruff: All checks passed.
- Strict mypy: Success, no issues in 117 source files.
- git diff --check: passed.
- git diff --cached --name-only: empty.
- find . -name '*.orig': empty.

Skips/deselects:
- Real LLM was explicitly excluded.
- Real external/postgres/staging tests remained under existing pytest marker guards.
- Real clamd gate remained skipped unless its explicit env gate is enabled.

## Modified files

Task5 production files:
- agent-server/focusproof/domain/evidence_facts.py
- agent-server/focusproof/openhands_adapter/events.py
- agent-server/focusproof/openhands_runtime/media_evidence_facts.py
- agent-server/focusproof/openhands_runtime/runtime_evidence_message_factory.py
- agent-server/focusproof/persistence/providers.py
- agent-server/focusproof/persistence/repositories.py

Task5 and compatibility tests:
- agent-server/tests/openhands_adapter/test_event_projection.py
- agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py
- agent-server/tests/openhands_runtime/test_multimodal_sdk_contracts.py
- agent-server/tests/openhands_runtime/tools/test_media_evidence.py
- agent-server/tests/persistence/test_media_uow.py
- agent-server/tests/test_media_message_content.py

Report:
- docs/research/AI5_7_TASK5_IMPLEMENTATION_REPORT.md

## Residual risks

- Task6 remains intentionally out of scope: no real visual provider integration, no live production ClamAV scan wiring, and no production deployment changes.
- Legacy rows that cannot prove clean remain non-contributing; this is deliberate and may require separate operator remediation if historical evidence should be re-admitted.
- The workspace contains extensive unrelated dirty/untracked work from prior tasks; this report lists only Task5 touched files and does not assert ownership over unrelated changes.

AI5_7_TASK5_IMPLEMENTATION_READY
