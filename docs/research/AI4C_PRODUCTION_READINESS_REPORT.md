# AI4C Production Readiness Report

ReleaseClassification: `staging-ready with blockers`

## Baseline, Branch, and Commits

Task 4 evidence was collected from accepted AI4C.3/Task 3 HEAD
`0a4afaeecd1cc489b6ef0e0e4efabd7a9adb6069` on
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
| AI4C-SDK-EQUIVALENCE | pass | `test_openhands_release_equivalence.py`; `scripts/check_openhands_release_equivalence.py --version 1.31.0 --timeout-seconds 300`: PASS; exact Task 4 digests below | AI4C.3 | goal: reproducible SDK source | Official release availability remains an external dependency. |
| AI4C-POSTGRESQL | pass | `pytest agent-server/tests/ai4c -m postgres -q`: 10 passed, 395 deselected | AI4C.3 | goal: PostgreSQL compatibility | Dedicated disposable PostgreSQL profile only. |
| AI4C-CLEAN-STACK | pass | accepted Task 3 gate at `0a4afae`: `test_staging_external_stack_builds_runs_and_preserves_ids`, 1 passed in 1529.73s; canonical digests below | AI4C.3 | goal: clean staging deployment | Local OIDC fixture is not an external issuer. |
| AI4C-PAIRED-RESTORE | pass | `test_staging_external_restores_paired_product_and_native_state_idempotently`: 1 passed in 15.21s | AI4C.3 | goal: paired recovery | Drill used disposable local PostgreSQL and native persistence. |
| AI4C-REDACTION | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"` includes `test_security_audit.py` | AI4C.2 | goal: secret/content redaction | Fixed sentinel hygiene scan remains at closure. |
| AI4C-ACCESSIBILITY | blocked | `frontend/e2e/ai4c-production-readiness.spec.ts` pending | AI4C.4 | goal: keyboard/focus/zoom/automated checks | Browser acceptance not yet authored. |
| AI4C-DETERMINISTIC-GATES | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"`; `npm test`; `npm run test:e2e`; `npm audit --omit=dev` | AI4C.4 | goal: full deterministic regression | 16 deprecation warnings and Vite CJS warning remain. |
| AI4C-REAL-PROVIDER | not-authorized | `agent-server/tests/ai4c/test_real_provider.py` live node not authorized | AI4C.1 | goal: authorized real-provider acceptance | No live call, cost, token, or latency evidence. |
| AI4C-EXTERNAL-OIDC-STAGING | blocked | `docs/superpowers/plans/2026-07-17-ai4c4-final-acceptance.md` Task 4 | AI4C.4 | goal: real external identity/staging | No approved issuer or non-public target. |
| AI4C-PROTOCOL-FREEZE | pass | `pytest <12 focused reuse/protocol/auth test files> -q`: 168 passed, 1 deselected, 3 warnings in 22.62s; construction and duplicate scans below | AI4C.1-3 | design: protocol freeze | SDK gaps remain version-sensitive and retain explicit deletion conditions. |
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

Task 5 audited the installed official `openhands-sdk==1.31.0`. The public
imports, installed implementation locations and FocusProof construction/use
sites are:

| SDK surface | Public import | Installed implementation | FocusProof construction/use |
| --- | --- | --- | --- |
| `Agent` | `from openhands.sdk import Agent` | `openhands.sdk.agent.agent` | `openhands_runtime/factory.py:177` |
| `LLM` | `from openhands.sdk import LLM` | `openhands.sdk.llm.llm` | `openhands_adapter/llm_config.py:23` |
| `LocalConversation` | `from openhands.sdk.conversation import LocalConversation` | `openhands.sdk.conversation.impl.local_conversation` | `openhands_runtime/factory.py:215`; public `Conversation` factory at line 202 |
| `EventLog` | `from openhands.sdk.conversation import EventLog` | `openhands.sdk.conversation.event_store` | SDK-owned `conversation.state.events`, read by `manager.py:207,245,281,392,563` |
| `View` | `from openhands.sdk.context.view import View` | `openhands.sdk.context.view.view` | SDK-owned `ConversationState.view`; no product reimplementation |
| `ToolDefinition` / `ToolExecutor` | `from openhands.sdk.tool import ToolDefinition, ToolExecutor` | `openhands.sdk.tool.tool` | `tool_registry.py` and the learner-input, review-draft, text, URL and verification tool modules |
| `ActionEvent` / `ObservationEvent` | `from openhands.sdk.event import ActionEvent, ObservationEvent` | `openhands.sdk.event.llm_convertible.action` / `.observation` | `manager.py`, `projector.py` and `result_extractor.py` consume native events |
| metrics | `ConversationState.stats.get_combined_metrics()` | SDK conversation stats/metrics | sanitized aggregate projection in `openhands_runtime/handle.py:49` |
| `interrupt()` / `close()` | public `LocalConversation` lifecycle | SDK local conversation | `manager.py:239,276,325,349,528`; construction-failure close at `factory.py:233,236` |
| `TestLLM` | `from openhands.sdk.testing import TestLLM` | `openhands.sdk.testing.test_llm` | deterministic factory/runtime and acceptance fixtures only |

The production-tree and package-metadata scan found no second Conversation,
runtime, EventLog, agent loop, Action/Observation/Tool protocol, provider HTTP
client, scheduler, or default programming-tool set. The deleted legacy adapter
agent/learning-conversation, runtime/persistence EventLog and fake/Web3 tool
paths remain absent from package metadata. `factory.py` sets
`include_default_tools=[]`. Its `httpx.Client` is the bounded URL-evidence
fetcher, not an LLM/provider client; provider construction remains the official
SDK `LLM` only. Product `audit_projection` stores are explicitly query
projections of native event IDs, not native fact stores.

The accepted SDK-gap records remain minimum-surface additions with deletion
conditions: select public `LocalConversation` directly only because the SDK
`Conversation` factory does not forward `max_budget_per_run`; delete that
branch when it does. Keep the process-wide provider `BoundedSemaphore` only
until the SDK supplies equivalent paid-provider admission. Keep the sanitized
metrics projection only until the SDK exposes an equivalent stable projection.
Keep the single-call URL deadline adapter only until the SDK provides a hard
deadline for synchronous `ToolExecutor`; it introduces no runtime, event log,
loop, cancellation token or tool protocol.

Protocol comparison used the accepted AI4B API/session tests and current
serialized responses. No success-response issuer or subject field was added.
`ownerUserId` remains the opaque resolver-generated `principal_*` identifier;
raw issuer/subject remain confined to the product principal-mapping boundary.
The only AI4C identity-boundary response differences are missing/invalid token
`401`, disabled identity `403`, and cross-owner/nonexistent indistinguishable
`404` responses.

Provider admission remains the product-owned process-wide bounded semaphore
around official `LocalConversation.arun()`. `VerifiedIdentity` is created only
after issuer, audience, signature, time and subject verification; authorization
binds product Session/Evidence/Answer/Review queries to the opaque principal.
Audit and operations rows are minimized product projections keyed to native
OpenHands event IDs. Paired backup/restore coordinates the product database and
OpenHands persistence as one recoverable unit and is idempotent on replay.

The focused Task 5 command removed all provider-key variables and ran reuse,
LLM operations, identity authorization/persistence/end-to-end, security audit,
operational telemetry, paired backup/restore, AI4B API session contract, native
tool contract and manager lifecycle tests: **168 passed, 1 deselected, 3
warnings in 22.62s**. The deselected node was the explicitly unauthorized live
provider test; warnings were one Starlette/httpx and two SQLite datetime
deprecations. No real LLM or external OIDC call ran.

## Identity, Threat Model, and Redaction

Pending deterministic and browser acceptance evidence. The local issuer is a
fixture only and cannot satisfy the external identity row.

## Staging, PostgreSQL, Migrations, and Recovery

Task 4 capability preflight passed on Linux `x86_64`: container CLI, Compose,
and PostgreSQL client were all `available`. The official OpenHands SDK 1.31.0
release-equivalence probe passed with signature digest
`f0dd4830554f256b605f565304d17221c7d2ad52fb33fa5afd6aa3823da48e3e`,
lifecycle digest
`ef16bc0b8164f579ae783b0d845c3947d539c285e426b532cf947b60993f5671`,
and event digest
`2fa64b778094febdae107c90c68edd31b8f7c460d08b277418c8811848285c66`.

The explicit PostgreSQL suite passed all 10 selected instances (395
deselected) in 13.16s. It covered migrations, rollback and ownership,
engine-restart native reference IDs, audit/review projections, and five
concurrent replay instances. The two exact FastAPI restart nodes passed in
11.85s and preserved completed-review and awaiting-user recovery.

The paired destructive drill passed in 15.21s. Its seed/first-restore/
second-restore snapshots were exactly equal for 2 session IDs, 2 distinct
owner IDs, 2 conversation IDs, 2 evidence IDs/hashes, 1 question/answer
version, 1 completed review ID/conversation/native-source ID, and both nonempty
native event ID/type lists. Review counts and per-conversation native-event
counts did not grow after the second restore; no user content was printed.

Per the Task 4 instruction, the accepted Task 3 two-round cold-stack gate at
`0a4afae` was cited rather than rerun. It passed in 1529.73s with identical
canonical digests: agent-server
`sha256:847371add386c19f67b4f017608aef2aac163f33e8bab55ca155ca64ba504e0e`
and frontend
`sha256:3f667ff29bff08bdc5ee16db045695ed853bbf4055be2e6ea1b6ab091caf5146`.
Both rounds traversed real browser Authorization Code + PKCE through BFF and
FastAPI, observed real `401`/`403`/`404`, and recovered official
`LocalConversation`; product events were `4 -> 7 -> 7`, native-source events
`3 -> 4 -> 4`, with completed review status.

## Accessibility

Pending Task 3 authenticated production-path acceptance.

## External Authorization and Blockers

A real LLM invocation was not authorized; no provider call was made and the
row remains `not-authorized`. No AI0-approved managed/self-hosted OIDC issuer or
identified non-public external staging target was supplied, so that row is
`blocked`. The maximum honest release classification is therefore
`staging-ready with blockers`.
