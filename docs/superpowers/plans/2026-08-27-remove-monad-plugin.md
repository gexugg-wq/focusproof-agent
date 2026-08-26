# Remove Monad Plugin Implementation Plan

Goal: remove active Monad while preserving general text, URL, image evidence, multi-turn review, scoring, OpenHands SDK runtime, persistence, demo, and real-provider gates.

## Frozen Scope
- Work only in WSL/Linux repo /home/holy/web3/focusproof-agent.
- Branch cleanup/remove-monad-plugin from clean local main@15083df.
- Do not read, print, or modify .env or secrets.
- Do not push, merge, amend, reset, clean, or rewrite history.
- Do not create or use Windows mirrors.
- Reuse OpenHands SDK directly; do not create a second Runtime, Conversation, EventLog, Action, Observation, Tool protocol, or registry.
- Do not alter generic scoring, public Evidence/Review protocol, text/URL/image evidence, media scanning, visual provider, or P0 demo-deterministic behavior.
- Preserve generic Web3 evidence type and wallet metadata unless git grep/import graph proves Monad-only use.
- Delete active Monad code, UI, buttons, scripts, contracts, dependency extras, env examples, and current deployment docs.
- Delete or rewrite Monad tests; do not skip or xfail them.
- Regenerate uv.lock with standard tooling only; do not hand-edit lock files.

## Delete Paths
- agent-server/focusproof/domain/plugins/monad/
- agent-server/tests/plugins/monad/
- frontend/features/plugins/monad/MonadEvidencePanel.tsx
- frontend/tests/monad-plugin.test.tsx
- frontend/e2e/monad-flow.spec.ts
- frontend/e2e/monad-disabled.spec.ts
- contracts/monad-learning-task/
- docs/deployment/MONAD_PLUGIN_LOCAL.md

## Keep Paths
- agent-server/migrations/versions/0004_monad_evidence_claims.py
- agent-server/migrations/versions/0005_media_artifacts.py
- agent-server/migrations/versions/0006_media_scan_audit_and_receipts.py
- agent-server/focusproof/openhands_runtime/**
- agent-server/focusproof/openhands_adapter/**
- agent-server/focusproof/domain/scoring.py and scoring_inputs.py
- agent-server/focusproof/domain/evidence_facts.py and review.py
- agent-server/focusproof/contracts/media_scan.py
- agent-server/focusproof/api/media_routes.py and media_models.py
- agent-server/focusproof/media_core/**, media_adapters/**, media_projection/**
- frontend/features/evidence/**, frontend/features/wallet/**, frontend/lib/wallet/config.ts
- historical docs under docs/research/** and docs/superpowers/** when clearly archival.

## Modify Paths
- agent-server/focusproof/domain/plugins/loader.py: remove Monad env gate and provider imports.
- agent-server/tests/plugins/test_plugin_boundary.py: assert no Monad provider or capability.
- agent-server/tests/plugins/test_plugin_disabled_imports.py: block Monad/web3 imports while default composition starts.
- agent-server/tests/plugins/test_plugin_enabled_loading.py: delete or rewrite enabled Monad path.
- agent-server/tests/ai4c/test_general_core_gate.py: remove disabled-env expectation, keep capability absence.
- agent-server/tests/ai5/test_real_visual_provider_product_path.py: remove obsolete Monad-disabled env injection.
- agent-server/tests/architecture/test_media_import_boundaries.py: remove obsolete focusproof/monad examples only.
- agent-server/tests/persistence/test_migrations.py: add old-db drop and data preservation tests.
- agent-server/migrations/versions/0007_drop_monad_evidence_claims.py: new forward migration.
- scripts/run_ai4b_test_server.py: delete only Monad imports/constants/provider/scenario; keep general/image E2E.
- scripts/run_demo_deterministic_server.py, run_general_core_gate.py, run_real_visual_provider_gate.py: remove Monad env assumptions or convert to no-capability checks.
- frontend/features/session/SessionWorkspace.tsx: remove Monad capability lookup and panel; keep generic evidence/wallet behavior.
- frontend/lib/api/contracts.ts: remove Monad-only metadata type.
- frontend/package.json and frontend/playwright.config.ts: remove monad E2E script/scenario.
- .env.example and frontend/.env.example: remove Monad env examples.
- pyproject.toml: remove monad optional extra.
- uv.lock: regenerate after pyproject change.
- README.md, contracts/README.md, current docs/gates: remove current product claims that Monad is available.
- docs/research/MONAD_PLUGIN_REMOVAL_REPORT.md: final report.

## Alembic Strategy
- Do not delete or edit 0004_monad_evidence_claims.py; deployed DBs may have applied it.
- Do not rewrite 0005_media_artifacts.py down_revision; it remains chained to 0004 as history.
- Add 0007_drop_monad_evidence_claims.py with down_revision 0006_media_scan_audit_and_receipts.
- upgrade() safely drops ix_monad_claim_session_evidence and monad_evidence_claims when present.
- downgrade() recreates the prior table, unique constraint, and index only for rollback compatibility.
- RED: old SQLite DB through 0006 has monad_evidence_claims; head must remove it while preserving generic session/evidence data.
- GREEN: SQLite upgrade, downgrade to 0006, and re-upgrade to head.
- PostgreSQL cycle runs only with authorized DSN already available without reading secrets; otherwise report not run.

## Tasks

### Task 1: Plan Commit
- Verify branch and clean status.
- Write this plan.
- Run wc and git diff --check.
- Commit docs: plan Monad plugin removal.

### Task 2: RED Backend Contracts
- Add tests proving plugin discovery never returns plugin_id monad or capability_id monad_learning_transaction.
- Add import-boundary test blocking focusproof.domain.plugins.monad and web3 while default composition starts.
- Run pytest on plugin boundary, disabled imports, enabled loading, and general core gate; expect failure before implementation.

### Task 3: GREEN Backend Removal
- Remove Monad branch from loader.
- Delete backend Monad plugin package and backend Monad tests.
- Delete or rewrite enabled-loading test so no Monad path remains.
- Run plugin and general-core targeted tests.
- Commit feat(backend): remove Monad plugin registration.

### Task 4: Migration Compatibility
- Keep 0004, 0005, and 0006 as historical chain nodes.
- Add migration tests for old DB forward drop, generic data preservation, downgrade, and re-upgrade.
- Add 0007 forward drop migration.
- Run SQLite migration tests and CLI upgrade/downgrade/re-upgrade.
- Run PostgreSQL migration cycle only if authorized config is already available.
- Commit feat(db): drop Monad claim table with forward migration.

### Task 5: Frontend Removal
- Add tests proving stale Monad capability does not render Monad UI or submit button.
- Keep tests proving generic Web3 evidence and wallet metadata payload still works if not Monad-only.
- Remove Monad component, unit tests, E2E tests, SessionWorkspace wiring, package script, and Playwright scenario.
- Run frontend targeted tests, lint, and typecheck.
- Commit feat(frontend): remove Monad UI slice.

### Task 6: Scripts, Contracts, Env, Deps, Docs
- Add tests proving run_ai4b_test_server.py --help exposes only general-flow and source has no Monad import.
- Remove only Monad branch from run_ai4b_test_server.py; keep general-flow, image unknown retry probe, migrations, and loopback host validation.
- Delete contracts/monad-learning-task and docs/deployment/MONAD_PLUGIN_LOCAL.md.
- Remove Monad env examples and pyproject optional extra.
- Regenerate uv.lock using uv lock or the repo standard.
- Update current README/docs/gates; keep archival docs where clearly historical.
- Run AI4B/AI5 gate tests, ruff, and mypy.
- Commit chore: remove Monad contracts env and gates.

### Task 7: Final Report
- Write docs/research/MONAD_PLUGIN_REMOVAL_REPORT.md with deletion counts, migration rationale, test evidence, grep closure, PostgreSQL status, and no-secret-read statement.
- Run full backend non-real-LLM suite.
- Run frontend lint, typecheck, unit, build, and general Playwright.
- Confirm browser coverage for text-only, text+PNG, and multi-turn review.
- Run grep closure and final git checks.
- Commit docs: report Monad plugin removal and stop for AI0.

## Acceptance Matrix
- Backend full: ./.venv/bin/python -m pytest
- Backend targeted: pytest plugins, migrations, domain, openhands_runtime, product capabilities, image evidence, AI4B release artifacts.
- Static: ruff check agent-server scripts; mypy agent-server/focusproof.
- SQLite migration: upgrade head, downgrade 0006_media_scan_audit_and_receipts, upgrade head.
- PostgreSQL migration: same cycle only with authorized non-secret config.
- Frontend: npm --prefix frontend run lint, typecheck, test, build, test:e2e:general.
- Browser: existing Playwright coverage for text-only, text+PNG, and multi-turn review.
- Git: git diff --check; git diff --cached --stat; git status --short.

## Grep Closure

Forbidden active-code hits:
- git grep -n -i monad -- agent-server/focusproof frontend scripts contracts .env.example frontend/.env.example pyproject.toml package.json frontend/package.json
- git grep -n FOCUSPROOF_MONAD
- git grep -n FOCUSPROOF_PLUGIN_MONAD_ENABLED
- git grep -n monad_learning_transaction
- git grep -n monad_transaction
- git grep -n verify_monad_learning_transaction

Allowed historical hits only:
- agent-server/migrations/versions/0004_monad_evidence_claims.py
- agent-server/migrations/versions/0005_media_artifacts.py down_revision only
- agent-server/migrations/versions/0007_drop_monad_evidence_claims.py
- migration compatibility tests mentioning old Monad schema
- clearly archival docs under docs/research/** and docs/superpowers/**

## Stop Conditions
- Stop and ask AI0 if OpenHands runtime/tool protocol changes appear necessary.
- Stop and report if branch is not cleanup/remove-monad-plugin or working tree contains unrelated dirty files.
- Do not enter follow-up phases after final report.
