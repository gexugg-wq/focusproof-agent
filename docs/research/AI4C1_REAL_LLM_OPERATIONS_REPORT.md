# AI4C.1 Real LLM Operations Report

## Baseline, Branch, and Commits

- Branch: `ai4c-production-readiness`.
- Accepted AI4C.0 baseline: `060ec815af6db5594d4c27d4c49985b65c358f13`.
- Runtime: Linux/WSL, Python 3.12.3, OpenHands SDK 1.31.0.
- AI4C.1 commits, in order:
  - `61a2f234cf631127a2b452269ce93690944f0979` — safe provider placeholder in the plan.
  - `74052f81479bff745eff2c127b0a6af00d34a5b0` — validated runtime profiles and bounded provider configuration.
  - `4cc577d71cba4c67530d35e0384b728c0acc2941` — context-window semantics aligned with the SDK.
  - `2778fcfc2205158e7b309e7e2493221b3daf0b35` — provider-neutral SDK `LLM` construction.
  - `aa4b72b90c416becf656d9753bf96a9f91425f4b` — native conversation cost bounds and safe usage projection.
  - `7f711cd72307f2e9998444e8d805ff5457f62b7b` — global paid-provider admission and failure closing.
  - `eaa5caa0897a860514069210854f5ce604dd3cd1` — separately authorized real-provider smoke contract.
  - `de383cf169c5828ad1002802ba792fd76ab0860b` — deterministic release-gate corrections found by the full gate.
  - Follow-up acceptance correction — runtime modules now import
    `LocalConversation` from the public `openhands.sdk.conversation` export
    rather than the private `impl` path.

## Changed Files

Relative to the AI4C.0 baseline, AI4C.1 owns these paths:

- `.env.example`
- `agent-server/focusproof/api/app.py`
- `agent-server/focusproof/config/profiles.py`
- `agent-server/focusproof/openhands_adapter/llm_config.py`
- `agent-server/focusproof/openhands_runtime/factory.py`
- `agent-server/focusproof/openhands_runtime/handle.py`
- `agent-server/focusproof/openhands_runtime/manager.py`
- `agent-server/focusproof/openhands_runtime/provider_admission.py`
- `agent-server/tests/ai4c/__init__.py`
- `agent-server/tests/ai4c/test_llm_operations.py`
- `agent-server/tests/ai4c/test_real_provider.py`
- `agent-server/tests/api/test_review_conversation_runtime.py`
- `agent-server/tests/openhands_runtime/test_concurrent_review_lock.py`
- `agent-server/tests/openhands_runtime/test_real_llm.py`
- `docs/project-management/TASK_BOARD.md`
- `docs/research/AI4C1_REAL_LLM_OPERATIONS_REPORT.md`
- `docs/superpowers/plans/2026-07-17-ai4c1-real-llm-operations.md`
- `pyproject.toml`

No frontend, scoring, contract, Event/Action/Observation/Tool protocol, Web3, or
multimodal path changed. No second Runtime, Conversation, EventLog, agent loop,
provider client, scheduler, or retry loop was added.

## Red/Green TDD Evidence

| Task | RED evidence | GREEN evidence | Commit |
| --- | --- | --- | --- |
| 1 | AI4C tests could not import `focusproof.config.profiles`. | Profile tests passed; deterministic ignores provider fields, staging/production fail closed, and secret values redact. | `74052f8` |
| 2 | Builder import was absent; the first 8,192 value then raised native `LLMContextWindowTooSmallError`. Four revised tests failed until production used the approved context setting. | SDK `LLM` constructs with a 16,384-token context window, 1,024 output cap, one retry, bounded waits/timeout, explicit pricing, no completion logging, and no short-context override. | `4cc577d`, `2778fcf` |
| 3 | `ProviderUsageSnapshot` was absent. The first typed constructor refactor also produced 22 Mypy errors from heterogeneous `**kwargs`. | 34 focused tests passed; explicit SDK constructors type-check, native `max_budget_per_run` is set, and only aggregate metrics are projected. | `aa4b72b` |
| 4 | Two modules failed collection because provider admission did not exist; a second RED proved SDK iterations were 6 rather than the four-call limit. | 33 focused reliability/concurrency tests passed. The second paid run is rejected before `arun`; the SDK iteration limit is `min(max_iterations, max_calls_per_review)`. | `7f711cd` |
| 5 | The new smoke contract could not import the exact-selection guard. A cross-test helper initially caused a duplicate-module Mypy error. | Guard tests: 3 passed, 1 real smoke deselected. Deterministic LLM regression: 11 passed. | `eaa5caa` |
| 6 | Full gate found the release secret sentinel classifying four numeric `TOKEN` settings as unsafe, then Mypy found one invariant-dict annotation. | Release artifacts: 13 passed. Focused correction: 24 passed. Full deterministic backend: 348 passed, 1 deselected. | `de383cf` |

Existing named reliability cases also prove provider-before-tool exceptions,
structured verification failures, UoW rollback, timeout, cancellation and safe
retry do not persist a completed ReviewResult or `review.completed` projection.
Model text alone still cannot manufacture tool success or completion facts.

## Exact Commands and Results

All commands ran from `/home/holy/web3/focusproof-agent` in WSL. Provider-key
variables and `ALLOW_SHORT_CONTEXT_WINDOWS` were removed; deterministic runs set
`LITELLM_LOCAL_MODEL_COST_MAP=true`. Repair 2 restored the fixed staging and
production contract: the exact explicit value is validated both as runtime
configuration and by a standard-library preflight before OpenHands/LiteLLM
imports. Local and deterministic profiles establish the same bundled-map
invariant only at that package boundary, not during benign FocusProof imports.

```bash
.venv/bin/python -m pytest agent-server/tests/ai4c/test_llm_operations.py \
  agent-server/tests/ai4b/test_reliability.py \
  agent-server/tests/openhands_runtime/test_concurrent_review_lock.py \
  agent-server/tests/openhands_runtime/test_runtime_failure.py -q
```

Result: 33 passed, 1 warning in 4.76s.

```bash
.venv/bin/python -m pytest agent-server/tests/ai4c/test_real_provider.py \
  -q -m "not real_llm"
```

Result: 3 passed, 1 deselected in 4.99s. The deselected node is the only live
provider smoke.

```bash
.venv/bin/python -m pytest agent-server/tests -q \
  -m "not real_llm and not postgres and not staging_external"
```

Final result: 348 passed, 1 deselected, 8 warnings in 31.31s. Warnings were one
Starlette/httpx deprecation and seven Python 3.12 SQLite datetime-adapter
deprecations. The separately authorized real-provider smoke was not run.

```bash
.venv/bin/ruff check agent-server
.venv/bin/mypy agent-server
git diff --check
```

Results: Ruff passed all files; Mypy reported no issues in 131 source files;
`git diff --check` passed. Tool versions were pytest 9.1.1, Ruff 0.15.21 and
Mypy 2.2.0.

## OpenHands APIs Reused

- `openhands.sdk.LLM` is the only LLM/provider client. DashScope remains a
  provider-neutral OpenAI-compatible configuration injected through the SDK;
  FocusProof performs no provider HTTP calls.
- Existing `openhands.sdk.Agent` remains the reasoning owner.
- `openhands.sdk.conversation.Conversation` remains the default factory path;
  `openhands.sdk.conversation.LocalConversation` is imported through the
  official public export, not the private `impl` path, and is used directly
  only to pass its public `max_budget_per_run` option.
- `LocalConversation.arun()`, `interrupt()`, `close()`, native state/EventLog,
  `ConversationStats.get_combined_metrics()` and SDK `Metrics` remain the run,
  lifecycle, fact and usage sources.
- Existing `openhands.sdk.event.ActionEvent` and `ObservationEvent`, plus the
  existing `ToolDefinition`/`ToolExecutor` path, remain unchanged.

An equivalence preflight compared the official
`openhands_sdk-1.31.0-py3-none-any.whl` (SHA-256
`fe690ce1c9ab14e1b505dd42f9f6504b3fa89cbf23cb7267a871e1c26f718085`)
against the installed accepted SDK source: all 267 `openhands/sdk/**/*.py`
files matched, with zero missing, changed or extra files. No custom wheel or SDK
source modification was introduced. Reproducible dependency provenance remains
an AI4C.3 gate.

## FocusProof-Owned SDK Gaps

1. The SDK `Conversation` factory does not expose
   `LocalConversation.max_budget_per_run`; FocusProof selects the public
   `LocalConversation` constructor only when a paid-policy budget exists.
   Delete this branch when the SDK factory forwards that option.
2. `BoundedProviderAdmission` applies a process-wide `BoundedSemaphore` before
   the existing `arun()` boundary. Delete it when the SDK provides an equivalent
   application-wide paid-provider admission primitive. Per-principal admission
   cannot be authoritative until AI4C.2 provides verified identities.
3. `RuntimeSettings` and `build_openhands_llm` are product configuration policy
   and a thin SDK-constructor adapter, not an LLM client. Remove the adapter if a
   future SDK public factory accepts the same validated, provider-neutral policy.
4. `ProviderUsageSnapshot` exposes only aggregate calls, tokens, cost and
   latency from public SDK metrics. Remove it if the SDK supplies an equivalent
   stable sanitized projection.

## Security and Secret Audit

- Default tests explicitly removed provider keys and deselected `real_llm`.
- No real provider request, real LLM key, Authorization header, raw provider
  payload or provider exception body was read, printed or persisted.
- `SecretStr` protects configured keys; completion logging is disabled.
- `ALLOW_SHORT_CONTEXT_WINDOWS` is rejected by production validation and smoke
  guard. The 16,384 value is an SDK context window, not a claimed per-request
  input quota. Existing HTTP/request/evidence size limits remain product input
  boundaries.
- Cost exposure is jointly bounded by SDK output tokens, SDK retries/timeouts,
  native conversation iteration and cost budgets, and FocusProof global
  admission. The live-call/token/cost assertions remain unobserved until AI0
  authorizes one exact smoke invocation.
- Existing release-artifact secret scanning passed. `.env.example` contains
  names and empty/approved placeholder values only; no `.env` was read.

## Migration and Rollback Evidence

AI4C.1 adds no database migration, schema, public HTTP success shape, Event,
Action, Observation, Tool or Review protocol change. Deterministic `TestLLM`
remains the default path. Rollback is therefore code-only: return to the accepted
AI4C.0 baseline or revert the ordered AI4C.1 commits. No data transformation or
native EventLog rewrite is required.

## Remaining Risks and Blockers

- **BLOCKED — live provider evidence:** AI0 did not authorize a real DashScope
  invocation in this run. The exact `-m real_llm` harness exists, but no live
  call count, token, cost or latency result is claimed.
- **BLOCKED — public deployment:** verified OIDC identity is AI4C.2 work. The
  current development anonymous identity remains a public-deployment blocker.
- Global admission is process-local. The approved staging topology is one
  FastAPI worker; multi-worker/distributed admission requires a later approved
  design, not a local scheduler.
- Provider price configuration is operator-supplied. A real smoke must validate
  observed aggregate cost under the approved USD 0.10 ceiling.
- Official-package reproducibility, PostgreSQL, OCI staging and external service
  recovery remain AI4C.3 work.
- Existing Starlette/httpx and SQLite deprecation warnings remain non-blocking
  maintenance debt.

## Stop Confirmation

AI4C.1 is locally complete and stops for AI0 acceptance. AI4C.2 was not opened
or implemented. No push, merge, public deployment, real-provider call,
multimodal work, Web3 work, wallet action, contract action or on-chain proof
occurred.
