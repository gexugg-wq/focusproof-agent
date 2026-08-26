# AI5 Multimodal Image Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task. Every task stops for AI0 review before staging or commit.

**Goal:** Implement design v7 with two-stage four/20 quotas, complete media lifecycle, existing synchronous UoW, conditional runtime contributions, modality-neutral narratives, and stable OpenHands messages. A real visual LLM is not an acceptance claim.

**Architecture:** Ingestion reserves count before bytes and rechecks actual normalized bytes under final DB lock. Conditional composition injects exact capabilities/tools/projection providers. Scoring consumes generic verified narratives. ArtifactResolvingLLM is a conditional Task5 design requiring an official public extension point before implementation.

**Tech stack:** Python 3.12, FastAPI/Starlette, SQLAlchemy/Alembic/PostgreSQL/SQLite, Pillow optional adapter, OpenHands SDK 1.31.0, LiteLLM, Next.js/TypeScript, pytest/Ruff/Mypy/Vitest/Playwright.

## Global constraints

Maximum four images/Session, 10 MiB original/image, 20 MiB distinct normalized total. Task 8 provider execution is environment-only and must not be described as real visual-LLM acceptance. Never modify dirty `.gitignore`/`frontend/.gitignore`; never stage, commit, push, merge, or amend without later AI0 authorization. Exact image logic is limited to `media_adapters/**`, `api/media_routes.py`, `runtime_evidence_message_factory.py`, `media_projection/image_narrative_provider.py`, `runtime_contributions.py`, `tools/media_evidence.py`, and `bootstrap/media_composition.py`. Manager/Agent loop, domain scoring, text/URL tools, general protocol, Domain Plugin, and Monad remain unchanged.

### Task 1: BASELINE architecture, SDK, and dependency contracts

**Files:** Create `agent-server/tests/architecture/test_media_import_boundaries.py`, `agent-server/tests/openhands_runtime/test_multimodal_sdk_contracts.py`; modify `docs/architecture/OPENHANDS_REUSE_STRATEGY.md`.

**Interfaces:** Verify public `Message`/`TextContent`/`ImageContent` model_dump/model_validate roundtrip, `TestLLM` completion/acompletion, public metrics mutation/read, `LocalConversation`, Agent identity on create/restore, required public `ToolDefinition` model fields and concrete registration behavior, process-isolated tool/model registration, and installed dependency facts. A child may inherit parent registrations; isolation proves only that registrations created inside the child do not leak back to the parent. Do not mark public inner composition, wrapper restore identity, stats/budget/call accounting, or the LocalConversation replacement-Agent negative case as proven.

- [x] BASELINE run from repository root: `.venv/bin/python -m pytest agent-server/tests/architecture/test_media_import_boundaries.py agent-server/tests/openhands_runtime/test_multimodal_sdk_contracts.py -q`. Expected PASS: inline forbidden import fixtures reject; allowed graph and installed public APIs/dependency versions satisfy assertions.
- [x] Record exact public contracts and SDK stop conditions; rerun the same command, expected PASS.
- [x] Stop for AI0 review; do not stage/commit.

### Task 2: Lifecycle ports, two-stage leases, migration, and existing UoW

**Files:** Create `agent-server/focusproof/media_core/models.py`, `ports.py`, `limits.py`, `ingestion.py`, `agent-server/migrations/versions/0005_media_artifacts.py`, `agent-server/tests/media_core/test_ingestion.py`, `test_crash_states.py`, `agent-server/tests/persistence/test_media_uow.py`, `test_media_postgres_concurrency.py`; modify `agent-server/migrations/env.py`, `agent-server/focusproof/persistence/models.py`, `repositories.py`, `unit_of_work.py`, `schema_check.py`, `agent-server/tests/persistence/test_migrations.py`, `test_unit_of_work.py`, root `pyproject.toml` marker list.

**Interfaces:** Exact v6 lifecycle ports; two synchronous transactions; `MediaIngestionCommand.ingest -> IngestedEvidenceResult`; existing UnitOfWorkFactory/Like and one SQL Session.

- [x] RED run `.venv/bin/python -m pytest agent-server/tests/media_core agent-server/tests/persistence/test_media_uow.py agent-server/tests/persistence/test_migrations.py -q`; expected failure for absent ports/schema. Tests include source ownership, normalized `rewind()` before stage, lifecycle/crash compensation, initial committed+reserved count, final actual distinct bytes, idempotency, SQLite serial behavior, and the `migrations/env.py` override contract. The migration tests prove `-x database_url` changes only the `/tmp` database, default database digest and mtime remain unchanged, omitted override uses `alembic.ini`, and empty/unknown/repeated/unparseable x arguments fail without emitting a credential-bearing URL.
- [x] GREEN implement and run exact isolated migration cycle from repository root: `.venv/bin/python -m alembic -c alembic.ini -x database_url=sqlite+pysqlite:////tmp/focusproof-ai5-migration.db upgrade 0005_media_artifacts`; then `.venv/bin/python -m alembic -c alembic.ini -x database_url=sqlite+pysqlite:////tmp/focusproof-ai5-migration.db downgrade 0004_monad_evidence_claims`; then `.venv/bin/python -m alembic -c alembic.ini -x database_url=sqlite+pysqlite:////tmp/focusproof-ai5-migration.db upgrade head`. Each expected exit 0; rerun RED command, expected PASS.
- [x] Run `.venv/bin/python -m pytest -m postgres_media agent-server/tests/persistence/test_media_postgres_concurrency.py -q` against approved disposable PostgreSQL. Expected PASS: with three committed, four concurrent allow one; two individually-fitting normalized uploads exceeding combined 20 MiB allow one.
- [x] Stop for AI0 review; do not stage/commit.

### Task 3: Codec/store adapters and real optional build contract

**Files:** Create `agent-server/focusproof/media_adapters/pillow_image_codec.py`, `local_quarantine_store.py`, `local_media_object_store.py`, `media_janitor.py`, matching tests; modify root `pyproject.toml`, `requirements/production.lock`, `deploy/agent-server.Dockerfile`, `deploy/compose.staging.yml`, `agent-server/tests/ai4c/test_staging_stack.py`, `test_safe_import_bootstrap.py`.

**Interfaces:** Validator/Normalizer; complete quarantine/staged store; media extra Pillow `>=12.1.1,<13`, python-multipart `>=0.0.20,<0.1`; single hash-locked production lock; core/media targets.

- [x] RED run `.venv/bin/python -m pytest agent-server/tests/media_adapters agent-server/tests/ai4c/test_staging_stack.py agent-server/tests/ai4c/test_safe_import_bootstrap.py -q`; expected missing adapters/build contract. Fixtures cover formats/security/normalization, seek reset, handles, manifests/DB-first janitor, disabled FocusProof import graph and enabled composition.
- [x] GREEN implement, regenerate `requirements/production.lock` hashes, rerun RED expected PASS. From repository root run `docker build --target core -f deploy/agent-server.Dockerfile -t focusproof-agent:core .` and `docker build --target media -f deploy/agent-server.Dockerfile -t focusproof-agent:media .`; both expected success, with feature/provider configuration selecting behavior rather than distribution absence.
- [x] Stop for AI0 review; do not stage/commit.

### Task 4: Streaming limits, multipart route, and product capability

**Files:** Create `agent-server/focusproof/api/request_limits.py`, `media_routes.py`, `media_models.py`, `agent-server/focusproof/bootstrap/media_composition.py`, API tests; modify `agent-server/focusproof/api/app.py`, `agent-server/focusproof/runtime/view.py`, `agent-server/tests/api/test_api_sessions.py`.

**Interfaces:** `APIRoute.matches` BodyLimitResolver; `asyncio.to_thread` command; `_view` backend capability.

- [x] RED run `.venv/bin/python -m pytest agent-server/tests/api/test_request_limits.py agent-server/tests/api/test_image_evidence.py agent-server/tests/api/test_product_capabilities.py agent-server/tests/api/test_api_sessions.py -q`; expected missing policy/route/capability. Cover FULL/PARTIAL/404/405/root_path/ambiguous, Content-Length/chunked, auth order, no caching, quotas, disabled clean process.
- [x] GREEN implement conditional composition and rerun RED plus text/URL startup, expected PASS.
- [x] Stop for AI0 review; do not stage/commit.

### Task 5: Stable synchronization, factory scope, and conditional wrapper design

**Files:** Create `agent-server/focusproof/openhands_runtime/runtime_evidence_message_factory.py`, `agent-server/focusproof/openhands_adapter/artifact_resolving_llm.py`, `model_image_resolver.py`, `model_capabilities.py`, adapter tests; modify `agent-server/focusproof/openhands_runtime/factory.py`, `synchronizer.py`, `evidence_messages.py`, `agent-server/focusproof/config/profiles.py`, `agent-server/focusproof/openhands_adapter/llm_config.py`, `.env.example`, existing factory/synchronizer/LLM-config tests.

**Interfaces:** String/Message factory; verified RuntimeLLMContext; an official public wrapper extension point if one exists; exact sync/async; typed quota fallback.

- [x] RED run `.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_factory.py agent-server/tests/openhands_runtime/test_message_synchronizer.py agent-server/tests/openhands_adapter -q`; expected missing v6 behavior. Cover four stable images, recovery dedup, scope, copies, four/20, cleanup, accounting/stats/budget, plus/flash same wrapper, one classified fallback.
- [x] HARD STOP: if no official public extension point proves inner composition, wrapper identity across recovery, stats/budget/call accounting, and the LocalConversation replacement-Agent negative case, record the SDK gap and stop Task5. Do not implement an OpenHands-style wrapper/facade and do not touch private state. Task2-4 may continue.
- [ ] GREEN only after the gate passes: N/A for this acceptance because the official OpenHands SDK 1.31.0 public extension gate did not pass. No wrapper/facade files are accepted; VisionInspectTool remains unregistered.
- [x] Stop for AI0 review; do not stage/commit.

### Task 6: Runtime contribution, safe media facts, and neutral narratives

**Files:** Create `agent-server/focusproof/openhands_runtime/runtime_contributions.py`, `tools/media_evidence.py`, `media_projection/image_narrative_provider.py`, and tests for contribution/tool/scoring inputs and narrative projection; modify composition wiring and generic narrative conversion only. No image condition enters Manager/Agent loop, domain scoring, text/URL tools, or Monad.

**Interfaces:** Exact RuntimeContribution; ScopedMediaEvidenceRepository/MediaEvidenceFacts; LearningNarrativeProjector/providers; scoring receives VerifiedLearningNarrative.

- [x] RED run `.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_media_runtime_contribution.py agent-server/tests/openhands_runtime/tools/test_media_evidence.py agent-server/tests/domain/test_media_scoring.py agent-server/tests/integration/test_image_review.py -q`; expected missing v6 contribution/projection. Cover ordered conflicts, delayed register_tool, disabled tool imports/map, owner mismatch, DTO secret/path absence, scoring matrix, and fake audio provider without scoring modification.
- [x] GREEN implement constructor-only injection, providers, tool, general narrative projector and scoring input conversion; rerun RED plus Manager/text/URL/scoring/Monad suites, expected PASS with loop/schema/caps unchanged. `scoring_inputs` is fully modality-neutral; narrative providers use the generic provider protocol.
- [x] Stop for AI0 review; do not stage/commit.

### Task 7: Transparent BFF and server-driven UI

**Files:** Create `frontend/features/evidence/ImageEvidenceForm.tsx`, test, `frontend/e2e/image-evidence.spec.ts`; modify `frontend/lib/api/client.ts`, `contracts.ts`, `errors.ts`, `proxy-timeout.ts`, `frontend/app/api/focusproof/[...path]/route.ts`, `frontend/features/evidence/EvidencePanel.tsx`, `frontend/features/session/SessionWorkspace.tsx`.

**Interfaces:** Multipart boundary-preserving request/BFF; dynamic UI from server capability.

- [x] RED run `cd frontend && npm test -- ImageEvidenceForm` and `cd frontend && npx playwright test e2e/image-evidence.spec.ts`; expected new cases fail. Cover bytes/boundary, JSON regression, capability off/on, four/10/20 display, upload/retry/accessibility/mobile.
- [x] GREEN implement and run `cd frontend && npm run lint && npm run typecheck && npm test && npm run build && npx playwright test e2e/image-evidence.spec.ts`; expected PASS.
- [x] Stop for AI0 review; do not stage/commit.

### Task 8: Backup/restore, regression, and approved real provider

**Files:** Create `agent-server/tests/ai5/test_real_image_provider.py`, `scripts/run_image_evidence_gate.py`, `docs/research/AI5_IMAGE_GATE_REPORT.md`; modify `scripts/ai4c_backup.py`, `scripts/ai4c_restore.py`, `agent-server/tests/ai4c/test_backup_restore.py`, `agent-server/tests/ai4c/test_real_provider.py`, `docs/deployment/STAGING.md`, root `pyproject.toml` marker list.

**Interfaces:** Manifest v2 DB/OpenHands/media unit; `real_llm` guarded image provider test; secret-safe gate.

- [x] RED run `.venv/bin/python -m pytest agent-server/tests/ai4c/test_backup_restore.py -q`; expected media-manifest failures. Implement isolation, digest/DB cross-check, rollback-safe switch, missing/hash mismatch fail-before-LLM, restored review rerun; rerun expected PASS.
- [x] Engineering verification completed: focused suites, migration up/down/up,
  backup/restore, frontend gates, report generation, Ruff, and diff gate passed.
  Historical full-suite evidence is retained in the final gate report.
- [ ] Real-provider gate pending: `.venv/bin/python -m pytest -m real_llm agent-server/tests/ai5/test_real_image_provider.py -q`; then `.venv/bin/python scripts/run_image_evidence_gate.py`. This remains deferred: no real visual provider was executed or accepted. Existing text regression remains historical evidence.
- [x] From repository root run `git diff --check` and `git status --short`; expected no diff errors, secret output, staging, or ignore-file change from AI5 work. Public-production malicious-file virus scanning remains deferred and must not be claimed complete.
- [x] Stop for AI0 final review; do not stage/commit.

Final checklist status: strict independent review `APPROVED` with no blocking,
important, or minor findings. Task 8 engineering/report/diff acceptance is
complete. Real-provider execution remains unchecked and deferred; no
public-production malicious-file scan is claimed.

## Self-review radius

Store: store/quarantine adapters/composition/tests. Codec: codec adapter/composition/fixtures. Provider: profiles/LLM config/composition/gate. OpenHands: adapter/message factory/factory/SDK tests/gap. Audio/PDF: codec/route/message mapping/runtime contribution/narrative provider using existing table/UoW, never Manager/Agent loop, domain scoring, text/URL tools, or Monad. Disable AI5: conditional route/capability/contribution/resolver off, nullable schema retained, disabled FocusProof media import graph clean.
