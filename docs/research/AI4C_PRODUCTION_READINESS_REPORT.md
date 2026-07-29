# AI4C Production Readiness Report

ReleaseClassification: `staging-ready with blockers`

## Baseline, Branch, and Commits

Evidence collection is in progress from accepted AI4C.3 HEAD
`f86a981997d60bccc25faf4c754b98971282e584` on
`ai4c-production-readiness`. AI4C.4 commits will be recorded at closure.

## Architecture and Scope

AI4C.4 adds acceptance evidence only. The product remains a general knowledge
learning-verification Agent and continues to use the official OpenHands SDK
runtime. No later-program work is in scope.

## Requirement Matrix

Each row has: requirement ID, current status, exact evidence locator, owning
phase, source requirement, and residual risk. A row is not `pass` until its
listed gate has actually run in this acceptance round.

| Requirement ID | Status | Evidence | Owning phase | Source | Residual risk |
| --- | --- | --- | --- | --- | --- |
| AI4C-RUNTIME-REUSE | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"` includes `test_openhands_reuse_boundary.py` | AI4C.1 | goal: OpenHands Direct-Reuse Gate | Task 5 construction-site audit remains. |
| AI4C-PROVIDER-BOUNDS | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"` includes `test_llm_operations.py` | AI4C.1 | goal: bounded provider policy | No live-provider observation. |
| AI4C-PROVIDER-FAILURES | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"` includes non-live `test_real_provider.py` nodes | AI4C.1 | goal: safe provider failure | External outage unobserved. |
| AI4C-AUTH-401 | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"` includes `test_identity_end_to_end.py` | AI4C.2 | goal: verified identity | Browser coverage remains Task 3. |
| AI4C-AUTH-403 | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"` includes `test_identity_end_to_end.py` | AI4C.2 | goal: disabled identity | Browser coverage remains Task 3. |
| AI4C-AUTH-404 | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"` includes `test_identity_end_to_end.py` | AI4C.2 | goal: ownership isolation | Browser coverage remains Task 3. |
| AI4C-SPOOF-RESISTANCE | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"` includes `test_identity_authorization.py` | AI4C.2 | goal: sender forgery/replay rejection | Real issuer remains blocked. |
| AI4C-ANONYMOUS-ISOLATION | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"` includes `test_identity_persistence.py` | AI4C.2 | goal: anonymous isolation | Anonymous profile remains local-dev only. |
| AI4C-SDK-EQUIVALENCE | blocked | `agent-server/tests/ai4c/test_openhands_release_equivalence.py` pending | AI4C.3 | goal: reproducible SDK source | Capability gate not yet run. |
| AI4C-POSTGRESQL | blocked | `agent-server/tests/ai4c/test_postgres_persistence.py` pending | AI4C.3 | goal: PostgreSQL compatibility | Capability gate not yet run. |
| AI4C-CLEAN-STACK | blocked | `frontend/e2e/ai4c-staging.spec.ts` pending | AI4C.3 | goal: clean staging deployment | Stack gate not yet run. |
| AI4C-PAIRED-RESTORE | blocked | `agent-server/tests/ai4c/test_backup_restore.py` pending | AI4C.3 | goal: paired recovery | Destructive drill not yet run. |
| AI4C-REDACTION | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"` includes `test_security_audit.py` | AI4C.2 | goal: secret/content redaction | Fixed sentinel hygiene scan remains at closure. |
| AI4C-ACCESSIBILITY | blocked | `frontend/e2e/ai4c-production-readiness.spec.ts` pending | AI4C.4 | goal: keyboard/focus/zoom/automated checks | Browser acceptance not yet authored. |
| AI4C-DETERMINISTIC-GATES | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"`; `npm test`; `npm run test:e2e`; `npm audit --omit=dev` | AI4C.4 | goal: full deterministic regression | 16 deprecation warnings and Vite CJS warning remain. |
| AI4C-REAL-PROVIDER | not-authorized | `agent-server/tests/ai4c/test_real_provider.py` live node not authorized | AI4C.1 | goal: authorized real-provider acceptance | No live call, cost, token, or latency evidence. |
| AI4C-EXTERNAL-OIDC-STAGING | blocked | `docs/superpowers/plans/2026-07-17-ai4c4-final-acceptance.md` Task 4 | AI4C.4 | goal: real external identity/staging | No approved issuer or non-public target. |
| AI4C-PROTOCOL-FREEZE | blocked | `agent-server/tests/ai4c/test_openhands_reuse_boundary.py` pending | AI4C.1-3 | design: protocol freeze | Audit not yet run. |
| AI4C-EXCLUSIONS | blocked | `docs/project-management/goals/AI4C_PRODUCTION_READINESS_CODEX_GOAL.md` Product Boundary | AI4C.4 | goal: exclusions | Final changed-path audit not yet run. |

## Red-Green History

- Evidence-lint RED: missing closure report, 2 failed in 0.06s. GREEN: 2 passed in 0.03s; commit `20eca49`.
- Collection-policy RED: default E2E produced 16 passed and 4 failed; named owning-phase node failed in 2.24s. GREEN: named node 1 passed in 2.13s and default E2E 16 passed in 1.1m; commit `8df1324`.
- Dependency RED: Next 15.5.18 and sharp 0.34.5 produced 2 high advisories. Next 15.5.21 plus scoped sharp 0.35.0 override produced `found 0 vulnerabilities`; commit `aa087c7`.

## Deterministic Gates and Versions

Linux versions: Python 3.12.3, OpenHands SDK 1.31.0, pytest 9.1.1, Ruff 0.15.21, Mypy 2.2.0, Node v18.19.1, npm 9.2.0, Next 15.5.21, Vitest 2.1.9, and Playwright 1.61.1.

- Backend pytest: 755 passed, 13 deselected, 16 warnings in 224.63s.
- Ruff: all checks passed. Mypy: no issues in 159 source files.
- Frontend lint and typecheck passed; Vitest: 6 files and 76 tests passed in 3.69s.
- Next 15.5.21 production build passed; default Playwright: 16 passed in 1.2m.
- `npm audit --omit=dev`: found 0 vulnerabilities.

Warnings were Starlette/httpx, cookie and SQLite datetime deprecations plus the Vitest Vite CJS API warning. No gate warning contained a secret value.

## OpenHands APIs Reused and SDK Gaps

Pending Task 5 public import/construction-site audit.

## Identity, Threat Model, and Redaction

Pending deterministic and browser acceptance evidence. The local issuer is a
fixture only and cannot satisfy the external identity row.

## Staging, PostgreSQL, Migrations, and Recovery

Pending Task 4 capability preflight and authorized local exercises.

## Accessibility

Pending Task 3 authenticated production-path acceptance.

## External Authorization and Blockers

A real provider invocation is not authorized. No real external OIDC issuer o
