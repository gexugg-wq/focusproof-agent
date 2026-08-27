# Monad Plugin Removal Report

Date: 2026-08-27
Branch: cleanup/remove-monad-plugin
Baseline: local main 15083df

## Summary

FocusProof active runtime, frontend UI/config, scripts/gates, contract sources, environment examples, and dependency metadata no longer expose the Monad plugin slice. General text, URL, image evidence, multi-turn review, generic scoring, OpenHands SDK Conversation/EventLog/tool protocol, persistence, demo-deterministic, and real-provider gates were preserved.

Change count from baseline: 49 deleted files, 37 modified files, 3 added files, 89 total changed paths. The largest deletion is the isolated contracts/monad-learning-task package and the backend/frontend plugin-owned test and UI trees.

## Deletions

- Removed agent-server/focusproof/domain/plugins/monad/.
- Removed agent-server/tests/plugins/monad/.
- Removed frontend/features/plugins/monad/MonadEvidencePanel.tsx.
- Removed frontend/e2e/monad-disabled.spec.ts, frontend/e2e/monad-flow.spec.ts, and frontend/tests/monad-plugin.test.tsx.
- Removed contracts/monad-learning-task/ and replaced contracts/README.md with a placeholder.
- Removed docs/deployment/MONAD_PLUGIN_LOCAL.md.

## Runtime And Config

- Plugin loader now returns no plugin providers by default or under legacy Monad env.
- SessionWorkspace no longer renders chain-specific plugin panels or button wiring.
- Frontend API contracts no longer carry Monad plugin metadata.
- .env.example, frontend Playwright configs, demo/gate scripts, README, pyproject.toml, and uv.lock no longer contain active Monad config or optional dependencies. uv.lock was regenerated with uv lock.
- scripts/run_ai4b_test_server.py keeps generic/image E2E support and only retains the image retry probe for the diagram.png retry fixture.

## Database Migration

Historical Alembic revisions 0004_monad_evidence_claims.py, 0005_media_artifacts, and 0006 remain intact for deployed database compatibility. New 0007_drop_monad_evidence_claims.py drops the obsolete monad_evidence_claims table on upgrade and restores a compatible structure on downgrade. Offline PostgreSQL SQL generation is supported without reflection against Alembic MockConnection.

Migration verification covered SQLite upgrade, downgrade to 0006_media_scan_receipts, and re-upgrade to head. PostgreSQL offline SQL compilation passed through existing identity/media audit tests. No live PostgreSQL DSN was used or read.

## Extra Test Server Fix

During removal verification, Playwright exposed a pre-existing coupling in scripts/run_ai4b_test_server.py: the general-flow TestLLM hard-coded a smoke text evidence id and fixed question text. That broke the official demo-deterministic image and question flows once the E2E matrix relied on the shared general server. The fix deduplicates the server by reusing build_demo_deterministic_test_llm and scopes the retry probe to the retry fixture filename only. No formal runtime, scoring, OpenHands SDK type, or media pipeline file was changed for this fix.

## Verification Evidence

- Backend full suite: ./.venv/bin/python -m pytest -q -> 1927 passed, 9 skipped, 14 deselected.
- Targeted backend removal/migration/gate suite -> 340 passed, 1 deselected; after format/migration repair targeted suite -> 154 passed.
- Migration targeted/offline PostgreSQL compile suite -> 93 passed.
- SQLite migration upgrade/downgrade/re-upgrade command passed.
- Ruff: ./.venv/bin/ruff check agent-server scripts passed.
- Scoped Ruff format check on branch-touched Python files passed after formatting only touched files. Full repository format check is not clean on baseline and was not applied globally.
- Mypy: ./.venv/bin/mypy agent-server/focusproof scripts passed.
- Frontend lint/typecheck/unit/build passed: npm --prefix frontend run lint, typecheck, test -- --run, build.
- Targeted browser checks passed: demo-deterministic review loop chromium 2 passed; bff-image-retry chromium 1 passed; general-complete-flow chromium 1 passed.
- Full browser matrix: npm --prefix frontend run test:e2e:general -> 25 passed.

## Grep Closure

Forbidden active paths checked with git grep -n -i monad -- agent-server/focusproof frontend scripts contracts pyproject.toml .env.example and returned no matches.

Allowed remaining matches are historical migrations, migration compatibility tests, explicit legacy env/import compatibility tests proving the plugin does not return, this removal report, and historical research/spec/plan documents. No active architecture fixture path now uses the retired plugin name. These are retained for database compatibility and audit history, not active runtime support.

## Residual Risk

- No live PostgreSQL upgrade/downgrade was run because no DSN was available and no secret files were read. Offline PostgreSQL migration SQL compilation passed.
- Historical docs still mention Monad by design; current architecture and active runtime/config/UI/scripts no longer expose it.
