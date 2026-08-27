# Monad History Cleanup Checklist

Goal: keep active runtime at zero Monad, keep old database upgrade continuity, and stop current authority docs from implying the retired plugin still exists.

Scope:
- Delete Monad-only design artifacts that are no longer needed by current runtime, migration chain, or authority docs.
- Update current authority docs to describe Monad as retired, not detachable/default-off.
- Keep 0004/0005/0007 migrations and minimal legacy compatibility tests.
- Keep scripts/run_ai4b_test_server.py as generic/image E2E infrastructure.
- Keep MONAD_PLUGIN_REMOVAL_REPORT.md as the retirement audit and align its wording with current script/runtime state.

Planned edits:
- Delete docs/superpowers/plans/2026-08-08-monad-evidence-plugin.md
- Delete docs/superpowers/specs/2026-08-08-monad-evidence-plugin-design.md
- Modify docs/README.md
- Modify docs/project-management/TASK_BOARD.md
- Modify docs/research/MONAD_PLUGIN_REMOVAL_REPORT.md

Verification:
- Active-scope Monad grep is zero.
- scripts/run_ai4b_test_server.py exists, contains no Monad text, and --help runs.
- Migration SQLite upgrade/downgrade/re-upgrade passes.
- Targeted release-artifact and migration tests pass.
- Backend targeted tests, Ruff, Mypy, frontend lint/typecheck, and at least one general Playwright E2E pass.
