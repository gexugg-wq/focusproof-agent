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
| AI4C-RUNTIME-REUSE | blocked | `agent-server/tests/ai4c/test_openhands_reuse_boundary.py` pending | AI4C.1 | goal: OpenHands Direct-Reuse Gate | Audit not yet run. |
| AI4C-PROVIDER-BOUNDS | blocked | `agent-server/tests/ai4c/test_llm_operations.py` pending | AI4C.1 | goal: bounded provider policy | Deterministic gate not yet run. |
| AI4C-PROVIDER-FAILURES | blocked | `agent-server/tests/ai4c/test_real_provider.py` pending | AI4C.1 | goal: safe provider failure | Contract gate not yet run. |
| AI4C-AUTH-401 | blocked | `agent-server/tests/ai4c/test_identity_end_to_end.py` pending | AI4C.2 | goal: verified identity | Acceptance gate not yet run. |
| AI4C-AUTH-403 | blocked | `agent-server/tests/ai4c/test_identity_end_to_end.py` pending | AI4C.2 | goal: disabled identity | Acceptance gate not yet run. |
| AI4C-AUTH-404 | blocked | `agent-server/tests/ai4c/test_identity_end_to_end.py` pending | AI4C.2 | goal: ownership isolation | Acceptance gate not yet run. |
| AI4C-SPOOF-RESISTANCE | blocked | `agent-server/tests/ai4c/test_identity_authorization.py` pending | AI4C.2 | goal: sender forgery/replay rejection | Acceptance gate not yet run. |
| AI4C-ANONYMOUS-ISOLATION | blocked | `agent-server/tests/ai4c/test_identity_persistence.py` pending | AI4C.2 | goal: anonymous isolation | Acceptance gate not yet run. |
| AI4C-SDK-EQUIVALENCE | blocked | `agent-server/tests/ai4c/test_openhands_release_equivalence.py` pending | AI4C.3 | goal: reproducible SDK source | Capability gate not yet run. |
| AI4C-POSTGRESQL | blocked | `agent-server/tests/ai4c/test_postgres_persistence.py` pending | AI4C.3 | goal: PostgreSQL compatibility | Capability gate not yet run. |
| AI4C-CLEAN-STACK | blocked | `frontend/e2e/ai4c-staging.spec.ts` pending | AI4C.3 | goal: clean staging deployment | Stack gate not yet run. |
| AI4C-PAIRED-RESTORE | blocked | `agent-server/tests/ai4c/test_backup_restore.py` pending | AI4C.3 | goal: paired recovery | Destructive drill not yet run. |
| AI4C-REDACTION | blocked | `agent-server/tests/ai4c/test_security_audit.py` pending | AI4C.2 | goal: secret/content redaction | Acceptance scan not yet run. |
| AI4C-ACCESSIBILITY | blocked | `frontend/e2e/ai4c-production-readiness.spec.ts` pending | AI4C.4 | goal: keyboard/focus/zoom/automated checks | Browser acceptance not yet authored. |
| AI4C-DETERMINISTIC-GATES | blocked | `pytest agent-server/tests -q` and `npm test` pending | AI4C.4 | goal: full deterministic regression | Full gates not yet run. |
| AI4C-REAL-PROVIDER | not-authorized | `agent-server/tests/ai4c/test_real_provider.py` live node not authorized | AI4C.1 | goal: authorized real-provider acceptance | No live call, cost, token, or latency evidence. |
| AI4C-EXTERNAL-OIDC-STAGING | blocked | `docs/superpowers/plans/2026-07-17-ai4c4-final-acceptance.md` Task 4 | AI4C.4 | goal: real external identity/staging | No approved issuer or non-public target. |
| AI4C-PROTOCOL-FREEZE | blocked | `agent-server/tests/ai4c/test_openhands_reuse_boundary.py` pending | AI4C.1-3 | design: protocol freeze | Audit not yet run. |
| AI4C-EXCLUSIONS | blocked | `docs/project-management/goals/AI4C_PRODUCTION_READINESS_CODEX_GOAL.md` Product Boundary | AI4C.4 | goal: exclusions | Final changed-path audit not yet run. |

## Red-Green History

To be completed with exact commands, failures, fixes, and commits.

## Deterministic Gates and Versions

Pending Task 2 execution.

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

A real provider invocation is not authorized. No real external OIDC issuer or
