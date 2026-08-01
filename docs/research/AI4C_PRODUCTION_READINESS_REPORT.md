# AI4C Production Readiness Report

ReleaseClassification: `staging-ready with blockers`

## Baseline, Branch, and Commits

The authoritative audit baseline is
`23a1a96460389147e6d477378f1d855a9a6a7187` (`docs: add AI4A Codex goal`).
Task 6 began on branch `ai4c-production-readiness` at accepted Task 5 HEAD
`59306c8afb15c65fc7dcec1151b9ff6ccc105fea`, with a clean worktree and only
the main worktree at `/home/holy/web3/focusproof-agent`. The complete 97-commit
chain from the baseline through Task 5 is recorded in the audit appendix.

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
| AI4C-ACCESSIBILITY | pass | `frontend/e2e/ai4c-production-readiness.spec.ts`; accepted `test_staging_external_stack_builds_runs_and_preserves_ids`, 1 passed in 1529.73s | AI4C.4 | goal: keyboard/focus/zoom/automated checks | Local issuer fixture only; no external production identity provider. |
| AI4C-DETERMINISTIC-GATES | pass | `pytest agent-server/tests -q -m "not real_llm and not postgres and not staging_external"`; `npm test`; `npm run test:e2e`; `npm audit --omit=dev` | AI4C.4 | goal: full deterministic regression | 16 deprecation warnings and Vite CJS warning remain. |
| AI4C-REAL-PROVIDER | not-authorized | `agent-server/tests/ai4c/test_real_provider.py` live node not authorized | AI4C.1 | goal: authorized real-provider acceptance | No live call, cost, token, or latency evidence. |
| AI4C-EXTERNAL-OIDC-STAGING | blocked | `docs/superpowers/plans/2026-07-17-ai4c4-final-acceptance.md` Task 4 | AI4C.4 | goal: real external identity/staging | No approved issuer or non-public target. |
| AI4C-PROTOCOL-FREEZE | pass | `pytest <12 focused reuse/protocol/auth test files> -q`: 168 passed, 1 deselected, 3 warnings in 22.62s; construction and duplicate scans below | AI4C.1-3 | design: protocol freeze | SDK gaps remain version-sensitive and retain explicit deletion conditions. |
| AI4C-EXCLUSIONS | pass | `docs/project-management/goals/AI4C_PRODUCTION_READINESS_CODEX_GOAL.md`; `git diff --name-only 23a1a96460389147e6d477378f1d855a9a6a7187...59306c8afb15c65fc7dcec1151b9ff6ccc105fea`; 194 paths audited | AI4C.4 | goal: exclusions | Baseline intentionally spans accepted AI4A/AI4B foundations and AI4C. |

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

Anonymous identity is **local-development-only** and is not an acceptable
production identity mechanism. The deterministic browser and API gates use a
local loopback HTTPS OIDC fixture. A production authentication provider has
**not been selected, integrated, or exercised**; no managed or standards-
compliant self-hosted external issuer and no non-public external staging target
were authorized by AI0. Local issuer evidence cannot pass the external row.

The tested threat model covers invalid/expired token `401`, disabled principal
`403`, cross-owner/nonexistent indistinguishable `404`, issuer/audience/time/
signature/subject verification, opaque principal mapping, sender spoof and
replay resistance, owner-bound Session/Evidence/Answer/Review access, bounded
JWKS caching, minimized audit projection, content/secret redaction, SSRF and
URL-resolution pinning, bounded provider admission, timeout and recovery, and
idempotent paired restore. Real issuer JWKS rotation/outage, production
provider operations, public ingress and real-provider behavior remain
unexercised residual threats.

No real key was read in this phase. Final commands remove supported provider-
key variables from their child environment; `.env` is excluded from inspection
and output.

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

The accepted Task 3 cold-stack gate at commit `0a4afae` ran the production
Next BFF, deterministic FastAPI/OpenHands runtime, PostgreSQL, and loopback
HTTPS OIDC fixture twice inside
`test_staging_external_stack_builds_runs_and_preserves_ids`: **1 passed in
1529.73s**. The dedicated Playwright node exercised authenticated text and URL
learning, awaiting-user answer, completed review, backend restart, refresh
recovery, `401`/`403`/`404`, retained input after authorization failure,
keyboard focus/Enter flow, accessible roles/names/live review status, axe with
**0 violations**, **200% zoom**, and the four accepted AI4B viewports
(`1440x900`, `1280x720`, `390x844`, `360x800`) with geometric overflow/overlap
checks. Successful `/api/focusproof/**` traffic used the real BFF/FastAPI path;
the spec contains no `page.route` or request interception.

## External Authorization and Blockers

A real LLM invocation was not authorized; no provider call was made and the
row remains `not-authorized`. No AI0-approved managed/self-hosted OIDC issuer or
identified non-public external staging target was supplied, so that row is
`blocked`. The maximum honest release classification is therefore
`staging-ready with blockers`.

## Product Boundary and Explicit Exclusions

FocusProof remains a general knowledge-learning authenticity-verification
Agent. This phase performed no Web3, contracts, wallet, on-chain, multimodal,
or public-deployment work. It did not create a second Runtime, Conversation,
EventLog, agent loop, Action/Observation/Tool protocol, provider HTTP client,
scheduler, or default programming-tool set. FocusProof directly reuses the
official OpenHands SDK **1.31.0 public APIs** described above; product database
ownership remains User/Session/Evidence/Answer/Review while OpenHands
Conversation/EventLog remains the owner of runtime facts.

## Migrations and Paired Restore

AI4C introduced Alembic migrations `0002_verified_principals.py` and
`0003_security_audit_events.py`. The PostgreSQL marker suite proved upgrade,
rollback, ownership, restart reference preservation, projections and
concurrent replay: **10 passed, 395 deselected in 13.16s**. The exact FastAPI
restart nodes passed **2 tests in 11.85s**. The destructive paired product-DB /
OpenHands-persistence drill passed **1 test in 15.21s** and reproduced equal
seed, first-restore and second-restore identity/reference snapshots without
duplicating review or native-event counts. Product data and native runtime
state must always be restored and rolled back as one paired unit.

## Changed-Path, Commit, and Hygiene Audit

The authoritative range is
`23a1a96460389147e6d477378f1d855a9a6a7187...HEAD`, not the obsolete SHA in
the original Task 6 example. At accepted Task 5 HEAD it contains **97 commits**
and **194 changed paths**. The paths cover the general FocusProof AI4A
verification foundation, AI4B quality/security/release foundation, AI4C
provider/identity/staging/acceptance work, tests, documentation, migrations,
deployment profiles, scripts and pinned dependencies. No `contracts/`, wallet,
on-chain or multimodal implementation path exists in the range. Task 6 itself
changes only `docs/research/AI4C_PRODUCTION_READINESS_REPORT.md`.

Complete abbreviated commit chain, oldest to newest (each abbreviation is
unambiguous in this repository):

```text
151cef4 0dd6844 c47a6b6 18fccf5 ccd5ced a8d3c0f 6d6aee2 4286774
85e8bfc 7f10a61 7a93546 05a93ac e53bd56 dfdef98 3fba404 6bd41e2
7634bd8 8307d18 1482eb8 00e3272 ad53843 1c8993c da70d41 f814c79
54013a9 4387333 8c04372 99de9aa c3ea003 9590a21 3048615 fa11900
8c416cd b5304b1 81b3692 04eb01d a7b9a84 d711ad1 994d50a f4ea096
7f678a0 32c82a3 0ca1984 2108481 90dfe00 bf5c9a8 4f73781 d93416e
7242f31 060ec81 61a2f23 74052f8 4cc577d 2778fcf aa4b72b 7f711cd
eaa5caa de383cf 08a3961 eb6cd33 cafeaec acb9dc9 5bf51ad 3162bc7
c03ea17 01573e7 a2fa4ce d5047b9 5932d1b 75a5027 dcf381b 8c2db19
00f019e 51e203d cd440db 5681e6c 2cf04fa 6a77ceb 06893a8 b973730
03465ff 2421154 27e3df1 d17f82d 8ba6a15 f86a981 20eca49 8df1324
aa087c7 79255f4 5eeaafc 4da266c 0eedb5f db4032a 0a4afae e89699a
59306c8
```

Final Task 6 evidence-lint, focused deterministic regression, Ruff, Mypy,
tracked-sentinel scan, `git diff --check`, worktree/process/resource cleanup,
final commit SHA and clean status are closure-time evidence and are recorded in
the commit/final acceptance handoff rather than preclaimed here.

## Rollback and Residual Risks

The report-only Task 6 commit can be reverted independently. The complete
AI4C.4 rollback revision before Task 6 is
`59306c8afb15c65fc7dcec1151b9ff6ccc105fea`; rolling back an owning-phase
repair requires rerunning every later-phase gate. Data rollback must use the
paired PostgreSQL/OpenHands recovery unit.

Residual blockers and risks are explicit: real LLM smoke is `not-authorized`;
external OIDC and public/non-public external staging exercise is `blocked`;
production auth-provider selection/integration is incomplete; real JWKS
rotation/outage and public ingress have no evidence; local disposable
PostgreSQL/Keycloak evidence does not substitute for operated production
services; OpenHands gap adapters remain version-sensitive; deprecation
warnings remain; and real provider latency/cost/failure behavior is unobserved.
These prevent any production or public-launch claim and fix the classification
at `staging-ready with blockers`.
