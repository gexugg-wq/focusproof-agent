# AI4C Production Readiness Report

ReleaseClassification: `staging-ready with blockers`

## Baseline, Branch, and Commits

The authoritative audit baseline is
`23a1a96460389147e6d477378f1d855a9a6a7187` (`docs: add AI4A Codex goal`).
Task 6 began on branch `ai4c-production-readiness` at accepted Task 5 HEAD
`59306c8afb15c65fc7dcec1151b9ff6ccc105fea`, with a clean worktree and only
the main worktree at `/home/holy/web3/focusproof-agent`. Before this Round 5
repair, the baseline-to-HEAD distance was 101 commits. This Round 5 repair is
delivered as two independent commits, making the final baseline-to-HEAD
distance 103 commits.

## Architecture and Scope

AI4C.4 adds acceptance evidence only. The product remains a general knowledge
learning-verification Agent and continues to use the official OpenHands SDK
runtime. No later-program work is in scope.

## Requirement Matrix

Each row has: requirement ID, current status, exact evidence locator, owning
phase, source requirement, and residual risk. `pytest-node` is an exactly
collectable executable locator; it does not by itself claim a run in this
repair round. `accepted-evidence` pins historical run evidence to an immutable
full commit and document anchor.

| Requirement ID | Status | Evidence | Owning phase | Source | Residual risk |
| --- | --- | --- | --- | --- | --- |
| AI4C-RUNTIME-REUSE | pass | pytest-node: agent-server/tests/ai4c/test_openhands_reuse_boundary.py::test_production_package_contains_no_parallel_event_log_or_agent_loop | AI4C.1 | goal: OpenHands Direct-Reuse Gate | Task 5 construction-site audit remains. |
| AI4C-PROVIDER-BOUNDS | pass | pytest-node: agent-server/tests/ai4c/test_llm_operations.py::test_factory_caps_native_iterations_to_provider_call_limit | AI4C.1 | goal: bounded provider policy | No live-provider observation. |
| AI4C-PROVIDER-FAILURES | pass | pytest-node: agent-server/tests/openhands_runtime/test_runtime_failure.py::test_run_failure_never_reports_openhands_usage | AI4C.1 | goal: safe provider failure | Deterministic exhausted-runtime failure proves no usage or false success; external outage remains unobserved. |
| AI4C-AUTH-401 | pass | pytest-node: agent-server/tests/ai4c/test_identity_end_to_end.py::test_real_signed_identity_chain_is_owner_isolated_and_identity_material_free | AI4C.2 | goal: verified identity | Browser coverage remains Task 3. |
| AI4C-AUTH-403 | pass | pytest-node: agent-server/tests/ai4c/test_identity_authorization.py::test_disabled_principal_is_forbidden_before_resource_lookup | AI4C.2 | goal: disabled identity | Browser coverage remains Task 3. |
| AI4C-AUTH-404 | pass | pytest-node: agent-server/tests/ai4c/test_identity_end_to_end.py::test_real_signed_identity_chain_is_owner_isolated_and_identity_material_free | AI4C.2 | goal: ownership isolation | Browser coverage remains Task 3. |
| AI4C-SPOOF-RESISTANCE | pass | pytest-node: agent-server/tests/ai4c/test_identity_authorization.py::test_verifier_rejects_wrong_kid_bad_signature_and_disallowed_algorithm | AI4C.2 | goal: sender forgery/replay rejection | Real issuer remains blocked. |
| AI4C-ANONYMOUS-ISOLATION | pass | pytest-node: agent-server/tests/ai4c/test_identity_persistence.py::test_storage_decision_isolates_anonymous_local_dev | AI4C.2 | goal: anonymous isolation | Anonymous profile remains local-dev only. |
| AI4C-SDK-EQUIVALENCE | blocked | doc: docs/research/AI4C_PRODUCTION_READINESS_REPORT.md#current-external-artifact-blockers | AI4C.3 | goal: reproducible SDK source | Requires authorization/network and retained artifacts to rerun the exact OpenHands SDK 1.31.0 fresh-venv release probe. |
| AI4C-POSTGRESQL | pass | pytest-node: agent-server/tests/ai4c/test_postgres_persistence.py::test_postgres_migrations_upgrade_downgrade_reupgrade_constraints_and_types | AI4C.3 | goal: PostgreSQL compatibility | Dedicated disposable PostgreSQL profile only. |
| AI4C-CLEAN-STACK | blocked | doc: docs/research/AI4C_PRODUCTION_READINESS_REPORT.md#current-external-artifact-blockers | AI4C.3 | goal: clean staging deployment | Requires two new clean-stack runs with retained auditable image artifacts and metadata; historical digests are background only. |
| AI4C-PAIRED-RESTORE | pass | pytest-node: agent-server/tests/ai4c/test_backup_restore.py::test_staging_external_restores_paired_product_and_native_state_idempotently | AI4C.3 | goal: paired recovery | Historical drill used disposable local PostgreSQL and native persistence. |
| AI4C-REDACTION | pass | pytest-node: agent-server/tests/ai4c/test_security_audit.py::test_authentication_failures_write_exactly_one_minimized_security_audit_row[headers0-missing_credentials-False] | AI4C.2 | goal: secret/content redaction | Fixed sentinel hygiene scan remains at closure. |
| AI4C-ACCESSIBILITY | pass | accepted-evidence: 76ea0fddd60dc61cc34b3ffe1faad0d84875221e:docs/research/AI4C_PRODUCTION_READINESS_REPORT.md#accessibility | AI4C.4 | goal: keyboard/focus/zoom/automated checks | Historical accepted run only; local issuer fixture only. |
| AI4C-DETERMINISTIC-GATES | pass | accepted-evidence: 76ea0fddd60dc61cc34b3ffe1faad0d84875221e:docs/research/AI4C_PRODUCTION_READINESS_REPORT.md#deterministic-gates-and-versions | AI4C.4 | goal: full deterministic regression | Historical accepted full-gate record; structure lint proves only report integrity. |
| AI4C-REAL-PROVIDER | not-authorized | pytest-node: agent-server/tests/ai4c/test_real_provider.py::test_dashscope_smoke_uses_native_bounded_conversation | AI4C.1 | goal: authorized real-provider acceptance | Node was not run; no live call, cost, token, or latency evidence. |
| AI4C-EXTERNAL-OIDC-STAGING | blocked | doc: docs/research/AI4C_PRODUCTION_READINESS_REPORT.md#current-external-artifact-blockers | AI4C.4 | goal: real external identity/staging | No approved issuer or non-public target. |
| AI4C-PROTOCOL-FREEZE | pass | pytest-node: agent-server/tests/ai4c/test_openhands_reuse_boundary.py::test_build_metadata_excludes_deleted_runtime_and_tracks_projection_stores | AI4C.1-3 | design: protocol freeze | SDK gaps remain version-sensitive and retain explicit deletion conditions. |
| AI4C-EXCLUSIONS | pass | doc: docs/research/AI4C_PRODUCTION_READINESS_REPORT.md#product-boundary-and-explicit-exclusions | AI4C.4 | goal: exclusions | Baseline intentionally spans accepted AI4A/AI4B foundations and AI4C. |

## Evidence Provenance for This Repair Round

This repair round runs the final-acceptance lint, the permitted AI4-focused
deterministic suite, Ruff, Mypy, and `git diff --check`. Those results are
closure evidence for this repair only. Historical external OIDC, PostgreSQL,
cold-stack, Playwright, and SDK release-probe results were not rerun. Historical
Playwright accessibility and complete deterministic-gate results remain cited
as accepted evidence because their anchors record auditable runs without relying
on bare digest claims. Historical SDK and clean-stack digest results are
background only and cannot establish current passes without their underlying
artifacts. The final-acceptance test is a structure and locator-integrity lint:
it proves matrix shape, exact pytest collection, document anchors, immutable
accepted-evidence anchors, and artifact digest binding; it does not prove that
every mapped gate ran in this round. No real LLM is authorized or run.

Current final-acceptance collected nodes: 18
Current marker-policy collected nodes: 5

The fresh combined marker-policy and final-acceptance gate passed **23 tests**;
its dynamically checked current split is 5 marker-policy nodes and 18
final-acceptance nodes. The complete default-marker AI4C directory run covered
every test file and reported **410 passed, 13 deselected, 3 warnings in
228.85s**, with zero failures. The non-AI4C agent-server regression reported
**365 passed, 14 warnings in 42.16s**. The affected OpenHands runtime suite
reported **165 passed in 6.59s**. Ruff passed, and Mypy reported no issues in
160 source files. `git diff --check` passed.

No remote LiteLLM cost-map fetch occurred. A cold subprocess collection guard
blocked and recorded every socket attempt while collecting both previously
unsafe test trees; both collections passed with no socket-attempt record. No
real LLM, external OIDC, PostgreSQL, or staging-external gate ran.

## Current External Artifact Blockers

Three evidence classes are intentionally distinct:

1. **Run in this repair:** deterministic semantic acceptance, marker-policy,
   AI4C regression, Ruff, Mypy, and diff hygiene commands are rerun locally.
2. **Historical background:** the earlier OpenHands SDK 1.31.0 probe digests
   and two-round clean-stack image digests describe previously accepted runs,
   but their wheel/environment and OCI image artifacts are not currently
   readable, retained, or recomputable. They are not current pass evidence.
3. **Current blockers:** `AI4C-SDK-EQUIVALENCE` requires authorization, network
   access, and retained auditable artifacts before rerunning the exact SDK
   1.31.0 fresh-venv release probe. `AI4C-CLEAN-STACK` requires two new
   clean-stack executions that retain the actual OCI image artifacts and
   metadata needed to recompute and compare their digests.

A digest of a summary JSON or Markdown report is not a substitute for an SDK
wheel/environment or Docker/OCI image and must not be used to clear either row.

## Red-Green History

- Round 5 P1 RED: 4 failed, 7 passed proved that compatibility restore mounted
  a transaction-specialized verifier and its executor recognized hash-shaped
  input. GREEN retained the historical OpenHands Tool/Action/Observation name
  required by SDK resume verification, but made its executor repository-backed
  and domain-neutral; the complete runtime suite passed 165 tests.
- Round 5 P2 RED: both default OpenHands-runtime and AI4B collection probes
  attempted outbound sockets before cost-map preflight. GREEN moved the
  deterministic profile/preflight to the root agent-server test collection
  boundary and both probes passed without a socket attempt.
- Round 5 freshness RED: the report exposed no current node counts while the
  collector found 18 final-acceptance and 5 marker-policy nodes. GREEN adds a
  dynamic collection comparison and the combined gate passed all 23 tests.

- Evidence-lint RED: missing closure report, 2 failed in 0.06s. GREEN: 2 passed in 0.03s; commit `20eca49`.
- Collection-policy RED: default E2E produced 16 passed and 4 failed; named owning-phase node failed in 2.24s. GREEN: named node 1 passed in 2.13s and default E2E 16 passed in 1.1m; commit `8df1324`.
- Dependency RED: Next 15.5.18 and sharp 0.34.5 produced 2 high advisories. Next 15.5.21 plus scoped sharp 0.35.0 override produced `found 0 vulnerabilities`; commit `aa087c7`.
- This repair's locator RED was **6 failed, 7 passed in 0.47s**: nonexistent,
  fabricated-class and wrong-parameter nodes plus bad/mismatched/missing digest
  evidence failed for their named reasons. The inherited marker-policy RED was
  **1 failed in 0.54s** at the exact starting HEAD because it bound collection
  to `1/75`; its GREEN asserts the sole selected full node ID and passed **5
  tests in 7.32s**. Final-acceptance GREEN was **13 passed in 40.55s**
  after exact pytest collection, artifact-bound digest verification, immutable
  accepted-evidence validation, and report locator corrections.

## Deterministic Gates and Versions

Linux versions: Python 3.12.3, OpenHands SDK 1.31.0, pytest 9.1.1, Ruff 0.15.21, Mypy 2.2.0, Node v18.19.1, npm 9.2.0, Next 15.5.21, Vitest 2.1.9, and Playwright 1.61.1.

- Backend pytest: 755 passed, 13 deselected, 16 warnings in 224.63s.
- Ruff: all checks passed. Mypy: no issues in 159 source files.
- Frontend lint and typecheck passed; Vitest: 6 files and 76 tests passed in 3.69s.
- Next 15.5.21 production build passed; default Playwright: 16 passed in 1.2m.
- `npm audit --omit=dev`: found 0 vulnerabilities.

Warnings were Starlette/httpx, cookie and SQLite datetime deprecations plus the Vitest Vite CJS API warning. No gate warning contained a secret value.

These counts are immutable historical accepted evidence from commit
`76ea0fddd60dc61cc34b3ffe1faad0d84875221e`, not reruns performed by this
repair round.

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
and PostgreSQL client were all `available`. As historical background, the Task
4 report recorded an OpenHands SDK 1.31.0 release-equivalence probe with
signature digest
`f0dd4830554f256b605f565304d17221c7d2ad52fb33fa5afd6aa3823da48e3e`,
lifecycle digest
`ef16bc0b8164f579ae783b0d845c3947d539c285e426b532cf947b60993f5671`,
and event digest
`2fa64b778094febdae107c90c68edd31b8f7c460d08b277418c8811848285c66`.
This repair did not run that fresh-venv release probe, and the artifacts behind
those bare digests are unavailable for recomputation, so SDK equivalence is
currently blocked. Direct SDK import, reuse, and compatibility tests remain
valid deterministic implementation evidence but do not prove release-artifact
equivalence.

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
`0a4afae` was cited rather than rerun. Its historical report recorded 1529.73s
and identical canonical digests: agent-server
`sha256:847371add386c19f67b4f017608aef2aac163f33e8bab55ca155ca64ba504e0e`
and frontend
`sha256:3f667ff29bff08bdc5ee16db045695ed853bbf4055be2e6ea1b6ab091caf5146`.
The actual image artifacts are not currently readable or recomputable, so this
is background rather than a current clean-stack pass. Two fresh runs with
retained auditable image artifacts and metadata are required to clear the row.
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
row remains `not-authorized`. SDK release-artifact equivalence and clean-stack
reproducibility are `blocked` until the exact reruns and retained artifacts in
the current blocker section exist. No AI0-approved managed/self-hosted OIDC
issuer or identified non-public external staging target was supplied, so that
row is also `blocked`. Deterministic implementation completion does not clear
these external release gates. The maximum honest release classification is
therefore `staging-ready with blockers`.

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

AI4 engineering is technically complete for the deterministic, locally tested
scope described above. External release remains blocked: engineering closure
does not authorize or prove a real provider, managed OIDC issuer, retained SDK
equivalence artifacts, clean external staging, or public deployment.

The report-only Task 6 commit can be reverted independently. The complete
AI4C.4 rollback revision before Task 6 is
`59306c8afb15c65fc7dcec1151b9ff6ccc105fea`; rolling back an owning-phase
repair requires rerunning every later-phase gate. Data rollback must use the
paired PostgreSQL/OpenHands recovery unit.

Residual blockers and risks are explicit: real LLM smoke is `not-authorized`;
SDK fresh-venv release equivalence and two-round clean-stack reproducibility are
`blocked` without new authorized runs and retained real artifacts; external
OIDC and public/non-public external staging exercise is `blocked`; production
auth-provider selection/integration is incomplete; real JWKS rotation/outage
and public ingress have no evidence; local disposable
PostgreSQL/Keycloak evidence does not substitute for operated production
services; OpenHands gap adapters remain version-sensitive; deprecation
warnings remain; and real provider latency/cost/failure behavior is unobserved.
These prevent any production or public-launch claim and fix the classification
at `staging-ready with blockers`.


## General Core Gate Status Appendix

This appendix records the general-core closure facts without altering the earlier AI4C report text.

- Monad plugin source is kept, but the default runtime disables it.
- Wallet, Monad, contract, and transaction-hash entry points only show when the enabled capability is present.
- Commit chain: `2950e14`, `8662a9d`, `f57c8b5`, `bbd7fc9`.
- `2950e14` restored structured runtime-unavailable responses.
- `8662a9d` hid wallet UI when Monad is disabled.
- `f57c8b5` corrected the DashScope/OpenAI-compatible model format to `openai/qwen-plus`.
- `bbd7fc9` kept API errors stable while logging a redacted root cause for server-side diagnostics.
- Deterministic local evidence: isolated Alembic DB PASS, backend `30 passed`, frontend `5 passed`, lint/typecheck/Ruff/Mypy/diff-check PASS, independent review APPROVED.
- Real-provider acceptance remains NOT PASSED / externally blocked: `qwen-plus` was the wrong model format, `openai/qwen-plus` reached DashScope but failed with free quota exhausted, and the OpenAI key was empty.
- Next gate: restore usable real-provider quota or credentials and rerun the two-subject official `/sessions/{id}/review` product path before any AI5 multimodal work.
- OpenHands is reused directly; no mirror loop, EventLog clone, or alternate protocol is introduced.
