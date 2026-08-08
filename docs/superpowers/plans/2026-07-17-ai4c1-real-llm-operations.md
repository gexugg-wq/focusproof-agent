# AI4C.1 Real-LLM Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run DashScope as the first bounded real provider through the existing OpenHands Conversation path while keeping deterministic tests credential-free and provider-neutral.

**Architecture:** A validated backend profile constructs OpenHands SDK `LLM` directly and passes bounded retry, token and price settings into the existing Agent/LocalConversation runtime. FocusProof adds only a pre-run admission policy and safe aggregate usage reporting; native events, tools and ReviewResult remain unchanged.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, OpenHands SDK 1.31.0 `LLM`/`Agent`/`LocalConversation`, LiteLLM through SDK, pytest, SDK `TestLLM`.

## Global Constraints

- Start only after AI0 accepts the AI4C.0 design and all five plans.
- Use WSL/Linux and `/home/holy/web3/focusproof-agent`.
- Do not read `.env`; tests pass explicit dictionaries and documentation edits `.env.example` only.
- DashScope uses the OpenHands SDK OpenAI-compatible route; do not add an HTTP provider client.
- Do not change API success shapes, Event/Action/Observation/Tool/Review protocols or scoring.
- Default tests unset every provider key and exclude `real_llm`.
- Real smoke requires a separate written AI0 authorization for the exact command.
- Do not push, merge or begin AI4C.2.

## File Ownership

**Create:**

- `agent-server/focusproof/config/profiles.py`
- `agent-server/focusproof/openhands_runtime/provider_admission.py`
- `agent-server/tests/ai4c/__init__.py`
- `agent-server/tests/ai4c/test_llm_operations.py`
- `agent-server/tests/ai4c/test_real_provider.py`
- `docs/research/AI4C1_REAL_LLM_OPERATIONS_REPORT.md`

**Modify only when named below:**

- `.env.example`
- `pyproject.toml` for marker registration only
- `agent-server/focusproof/config/env.py`
- `agent-server/focusproof/openhands_adapter/llm_config.py`
- `agent-server/focusproof/openhands_runtime/factory.py`
- `agent-server/focusproof/openhands_runtime/handle.py`
- `agent-server/focusproof/openhands_runtime/manager.py`
- `agent-server/focusproof/api/app.py`
- `agent-server/tests/openhands_adapter/test_llm_config.py`
- `agent-server/tests/openhands_runtime/test_real_llm.py`
- focused runtime failure, concurrency and lifecycle tests

Frontend, persistence models/migrations, scoring and protocol documents are not
owned by AI4C.1.

## OpenHands Reuse Acceptance Gate

Before every runtime Task, inspect and cite the relevant OpenHands SDK 1.31.0
public API. When the SDK already provides the required capability, use it
directly. Any local imitation of an Agent, Conversation, EventLog, Action,
Observation, Tool protocol or LLM client fails AI4C.1 acceptance.

## Fixed Interfaces

The ellipses in this code block are Python typing-stub notation that displays
signature contracts only. The corresponding Task steps define the complete
red test, implementation and verification work; the ellipses are not an
alternative implementation or a second runtime.

```python
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, SecretStr

RuntimeProfile = Literal["deterministic-test", "local-dev", "staging", "production"]


class RealLlmPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    model: str
    base_url: str
    api_key: SecretStr
    request_timeout_seconds: int
    num_retries: int
    retry_min_wait_seconds: int
    retry_max_wait_seconds: int
    context_window_tokens: int
    max_output_tokens: int
    max_iterations: int
    max_review_seconds: int
    max_concurrent_reviews: int
    admission_timeout_seconds: float
    max_calls_per_review: int
    max_cost_usd: float
    input_cost_per_token: float
    output_cost_per_token: float


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(frozen=True)
    profile: RuntimeProfile
    real_llm: RealLlmPolicy | None


def load_runtime_settings(environ: Mapping[str, str]) -> RuntimeSettings: ...


class ProviderAdmission(Protocol):
    def acquire(self) -> AbstractContextManager[None]: ...
```

`deterministic-test` has no `RealLlmPolicy`. `local-dev` may omit it. `staging`
and `production` require every field and fail validation otherwise.

## Test Helper Contracts

Define these helpers in `agent-server/tests/ai4c/test_llm_operations.py` before
the first test that calls them; do not leave them as implied fixtures:

```python
def complete_fake_dashscope_environment() -> dict[str, str]: ...
def fake_real_llm_policy(**overrides: object) -> RealLlmPolicy: ...
def create_real_mode_handle_with_fake_sdk_llm(*, max_cost_usd: float) -> ConversationHandle: ...
def handle_with_recorded_sdk_metrics() -> ConversationHandle: ...
def usage_snapshot_from(handle: ConversationHandle) -> ProviderUsageSnapshot: ...
def run_review_with_sdk_timeout() -> RuntimeReviewResult: ...
def persisted_completed_review_count() -> int: ...
def retry_with_scripted_sdk_success() -> RuntimeReviewResult: ...
def native_tool_call_ids_are_unique() -> bool: ...
```

`complete_fake_dashscope_environment()` supplies every `FOCUSPROOF_LLM_*`
field with non-secret fake values. `fake_real_llm_policy()` applies named
overrides to that validated mapping. The handle builders use the existing
runtime factory with SDK `TestLLM`; they never instantiate another loop or
event store. `RuntimeReviewResult` and `ConversationHandle` are the existing
runtime types. Persistence helpers query the existing UoW seeded by the test
fixture. The usage helper reads SDK metrics and returns only
`calls`, `input_tokens`, `output_tokens`, `cost_usd`, and `latency_seconds`.

Define these helpers in `agent-server/tests/ai4c/test_real_provider.py` before
the marked test:

```python
def require_exact_real_llm_selection(request: pytest.FixtureRequest) -> None: ...
def run_one_general_learning_review() -> tuple[ReviewResult, ProviderUsageSnapshot]: ...
```

The selection guard checks the explicit pytest marker expression and the
AI0-authorized environment switch without printing it. The review helper calls
the production factory/manager and native OpenHands Conversation path once;
`ProviderUsageSnapshot` is the production aggregate type defined in Task 3.

### Task 1: Runtime Profile and Secret-Free Configuration

**Files:**
- Create: `agent-server/focusproof/config/profiles.py`
- Modify: `agent-server/focusproof/config/env.py`
- Modify: `.env.example`
- Test: `agent-server/tests/ai4c/test_llm_operations.py`
- Test: `agent-server/tests/openhands_adapter/test_llm_config.py`

**Interfaces:**
- Produces: `load_runtime_settings(environ: Mapping[str, str]) -> RuntimeSettings`
- Consumes: explicit mappings only; no implicit dotenv read

- [ ] **Step 1: Write failing profile tests**

```python
def test_deterministic_profile_ignores_provider_values() -> None:
    settings = load_runtime_settings({
        "FOCUSPROOF_PROFILE": "deterministic-test",
        "DASHSCOPE_API_KEY": "placeholder",
    })
    assert settings.profile == "deterministic-test"
    assert settings.real_llm is None


def test_staging_profile_requires_every_real_llm_bound() -> None:
    with pytest.raises(ValidationError, match="FOCUSPROOF_LLM_MODEL"):
        load_runtime_settings({"FOCUSPROOF_PROFILE": "staging"})


def test_staging_profile_builds_provider_neutral_policy() -> None:
    settings = load_runtime_settings(complete_fake_dashscope_environment())
    assert settings.real_llm is not None
    assert settings.real_llm.provider == "openai-compatible"
    assert settings.real_llm.api_key.get_secret_value() == "fake-dashscope-key"
    assert "fake-dashscope-key" not in settings.model_dump_json()
```

`complete_fake_dashscope_environment()` returns explicit fake values for every
field in `RealLlmPolicy`, including `LITELLM_LOCAL_MODEL_COST_MAP=true`.

- [ ] **Step 2: Run the red tests**

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_llm_operations.py \
  agent-server/tests/openhands_adapter/test_llm_config.py -q
```

Expected: FAIL during collection because `focusproof.config.profiles` does not
exist.

- [ ] **Step 3: Implement the validated mapping loader**

Implement the fixed models and a loader that reads only its `environ` argument,
uses exact `FOCUSPROOF_LLM_*` names, rejects unknown profiles, validates positive
bounds and requires `LITELLM_LOCAL_MODEL_COST_MAP=true` for staging/production.
Keep `load_project_env()` only for explicit local-development compatibility;
production construction must no longer call it.

Add these safe names to `.env.example`:

```text
FOCUSPROOF_PROFILE=local-dev
FOCUSPROOF_LLM_PROVIDER=
FOCUSPROOF_LLM_MODEL=
FOCUSPROOF_LLM_BASE_URL=
FOCUSPROOF_LLM_API_KEY=
FOCUSPROOF_LLM_REQUEST_TIMEOUT_SECONDS=30
FOCUSPROOF_LLM_NUM_RETRIES=1
FOCUSPROOF_LLM_RETRY_MIN_WAIT_SECONDS=1
FOCUSPROOF_LLM_RETRY_MAX_WAIT_SECONDS=4
FOCUSPROOF_LLM_CONTEXT_WINDOW_TOKENS=16384
FOCUSPROOF_LLM_MAX_OUTPUT_TOKENS=1024
FOCUSPROOF_LLM_MAX_ITERATIONS=6
FOCUSPROOF_LLM_MAX_REVIEW_SECONDS=60
FOCUSPROOF_LLM_MAX_CONCURRENT_REVIEWS=1
FOCUSPROOF_LLM_ADMISSION_TIMEOUT_SECONDS=1
FOCUSPROOF_LLM_MAX_CALLS_PER_REVIEW=4
FOCUSPROOF_LLM_MAX_COST_USD=0.10
FOCUSPROOF_LLM_INPUT_COST_PER_TOKEN=0
FOCUSPROOF_LLM_OUTPUT_COST_PER_TOKEN=0
LITELLM_LOCAL_MODEL_COST_MAP=true
```

- [ ] **Step 4: Run focused green tests**

Repeat Step 2. Expected: all configuration tests PASS and no test accesses the
project `.env`.

- [ ] **Step 5: Run regression and commit**

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY .venv/bin/python -m pytest \
  agent-server/tests/openhands_adapter/test_llm_config.py \
  agent-server/tests/ai4c/test_llm_operations.py -q
git diff --check
git add .env.example agent-server/focusproof/config/env.py \
  agent-server/focusproof/config/profiles.py \
  agent-server/tests/ai4c agent-server/tests/openhands_adapter/test_llm_config.py
git commit -m "feat: validate bounded real LLM profiles"
```

### Task 2: SDK LLM Construction and Local Cost Metadata

**Files:**
- Modify: `agent-server/focusproof/openhands_adapter/llm_config.py`
- Modify: `agent-server/focusproof/openhands_runtime/factory.py`
- Test: `agent-server/tests/ai4c/test_llm_operations.py`
- Test: `agent-server/tests/openhands_runtime/test_runtime_failure.py`

**Interfaces:**
- Produces: `build_openhands_llm(policy: RealLlmPolicy, usage_id: str) -> openhands.sdk.LLM`
- Preserves: `LLMFactory = Callable[[str], LLM]` for SDK `TestLLM` injection

- [ ] **Step 1: Write failing SDK-construction tests**

```python
def test_build_openhands_llm_uses_sdk_and_every_bound() -> None:
    policy = fake_real_llm_policy()
    llm = build_openhands_llm(policy, usage_id="focusproof-test")
    assert isinstance(llm, LLM)
    assert llm.model == policy.model
    assert llm.base_url == policy.base_url
    assert llm.num_retries == 1
    assert llm.timeout == 30
    assert llm.max_input_tokens == policy.context_window_tokens == 16384
    assert llm.max_output_tokens == 1024
    assert llm.log_completions is False
    assert llm.input_cost_per_token == policy.input_cost_per_token
    assert llm.output_cost_per_token == policy.output_cost_per_token


def test_llm_config_and_repr_do_not_expose_api_key() -> None:
    llm = build_openhands_llm(fake_real_llm_policy(), usage_id="safe")
    rendered = repr(llm) + llm.model_dump_json()
    assert "fake-dashscope-key" not in rendered
```

- [ ] **Step 2: Run tests and observe failure**

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY LITELLM_LOCAL_MODEL_COST_MAP=true \
  .venv/bin/python -m pytest agent-server/tests/ai4c/test_llm_operations.py -q
```

Expected: FAIL because `build_openhands_llm` is not defined.

- [ ] **Step 3: Implement SDK-only construction**

```python
def build_openhands_llm(policy: RealLlmPolicy, usage_id: str) -> LLM:
    return LLM(
        usage_id=usage_id,
        model=policy.model,
        api_key=policy.api_key,
        base_url=policy.base_url,
        num_retries=policy.num_retries,
        retry_min_wait=policy.retry_min_wait_seconds,
        retry_max_wait=policy.retry_max_wait_seconds,
        timeout=policy.request_timeout_seconds,
        # SDK max_input_tokens is the model context window, not a per-call quota.
        max_input_tokens=policy.context_window_tokens,
        max_output_tokens=policy.max_output_tokens,
        input_cost_per_token=policy.input_cost_per_token,
        output_cost_per_token=policy.output_cost_per_token,
        log_completions=False,
        stream=False,
    )
```

`ConversationFactory` receives an immutable `RuntimeSettings`. Production LLM
creation calls this function; injected `LLMFactory` remains restricted to SDK
`TestLLM` in deterministic tests.

OpenHands SDK 1.31.0 requires an effective context window of at least 16,384
tokens. `ALLOW_SHORT_CONTEXT_WINDOWS` is prohibited and the implementation must
not read or set it. FocusProof does not add a tokenizer, prompt truncator or
provider client to claim a separate per-request input-token quota. Existing
HTTP/request/Evidence size bounds constrain product input; output, cost, call,
timeout, retry and admission controls constrain paid execution.

- [ ] **Step 4: Run focused green and failure tests**

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY LITELLM_LOCAL_MODEL_COST_MAP=true \
  .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_llm_operations.py \
  agent-server/tests/openhands_runtime/test_runtime_failure.py -q
```

Expected: PASS; constructing the test LLM performs no network call.

- [ ] **Step 5: Commit**

```bash
git add agent-server/focusproof/openhands_adapter/llm_config.py \
  agent-server/focusproof/openhands_runtime/factory.py \
  agent-server/tests/ai4c/test_llm_operations.py \
  agent-server/tests/openhands_runtime/test_runtime_failure.py
git commit -m "feat: construct provider-neutral OpenHands LLM"
```

### Task 3: Native Conversation Budget and Safe Usage Snapshot

**Files:**
- Modify: `agent-server/focusproof/openhands_runtime/factory.py`
- Modify: `agent-server/focusproof/openhands_runtime/handle.py`
- Modify: `agent-server/focusproof/openhands_runtime/manager.py`
- Test: `agent-server/tests/ai4c/test_llm_operations.py`
- Test: `agent-server/tests/openhands_runtime/test_conversation_lifecycle.py`

**Interfaces:**
- Produces: `ProviderUsageSnapshot(call_count: int, input_tokens: int, output_tokens: int, cost_usd: float, latency_seconds: float)`
- Uses: public `LocalConversation(max_budget_per_run=...)` and `conversation.state.stats`

- [ ] **Step 1: Write red budget and usage tests**

```python
def test_factory_sets_public_local_conversation_budget() -> None:
    handle = create_real_mode_handle_with_fake_sdk_llm(max_cost_usd=0.10)
    assert isinstance(handle.conversation, LocalConversation)
    assert handle.conversation.max_budget_per_run == 0.10


def test_usage_snapshot_contains_aggregates_only() -> None:
    snapshot = usage_snapshot_from(handle_with_recorded_sdk_metrics())
    assert snapshot.call_count == 2
    assert snapshot.input_tokens == 120
    assert snapshot.output_tokens == 30
    assert snapshot.cost_usd == pytest.approx(0.004)
    assert set(snapshot.model_dump()) == {
        "call_count", "input_tokens", "output_tokens", "cost_usd", "latency_seconds"
    }
```

- [ ] **Step 2: Run named tests and observe failure**

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY LITELLM_LOCAL_MODEL_COST_MAP=true \
  .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_llm_operations.py::test_factory_sets_public_local_conversation_budget \
  agent-server/tests/ai4c/test_llm_operations.py::test_usage_snapshot_contains_aggregates_only -q
```

Expected: FAIL because the factory does not pass a budget and the snapshot type
does not exist.

- [ ] **Step 3: Record and implement the SDK gap**

Record that SDK 1.31.0 `LocalConversation` exposes `max_budget_per_run`, while
`Conversation.__new__` does not forward it. Add one helper that directly
constructs the public `LocalConversation` with the existing parameters plus the
budget. Do not copy `arun`, state, event or lifecycle code.

Derive the immutable aggregate snapshot from public SDK Conversation stats.
Do not place model, provider, base URL, prompt, completion or credential in the
snapshot or ReviewResult.

Deletion condition: use `Conversation(...)` again when the pinned factory
forwards `max_budget_per_run` and these tests pass.

- [ ] **Step 4: Run green and lifecycle regression tests**

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY LITELLM_LOCAL_MODEL_COST_MAP=true \
  .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_llm_operations.py \
  agent-server/tests/openhands_runtime/test_conversation_lifecycle.py \
  agent-server/tests/openhands_runtime/test_native_event_flow.py -q
```

Expected: PASS and native Action/Observation assertions remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add agent-server/focusproof/openhands_runtime/factory.py \
  agent-server/focusproof/openhands_runtime/handle.py \
  agent-server/focusproof/openhands_runtime/manager.py \
  agent-server/tests/ai4c/test_llm_operations.py \
  agent-server/tests/openhands_runtime/test_conversation_lifecycle.py
git commit -m "feat: enforce native conversation cost bounds"
```

### Task 4: Product Admission, Provider Failure, and Retry Idempotency

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/provider_admission.py`
- Modify: `agent-server/focusproof/openhands_runtime/manager.py`
- Modify: `agent-server/focusproof/api/app.py`
- Test: `agent-server/tests/ai4c/test_llm_operations.py`
- Test: `agent-server/tests/ai4b/test_reliability.py`
- Test: `agent-server/tests/openhands_runtime/test_concurrent_review_lock.py`

**Interfaces:**
- Produces: `BoundedProviderAdmission(max_concurrent: int, acquire_timeout_seconds: float)`
- Produces: `ProviderAdmissionUnavailableError`
- Preserves: existing Session lock and public service-unavailable response shape

- [ ] **Step 1: Write barrier-based red tests**

```python
def test_global_provider_admission_rejects_second_paid_run_before_llm() -> None:
    admission = BoundedProviderAdmission(max_concurrent=1, acquire_timeout_seconds=0.01)
    with admission.acquire():
        with pytest.raises(ProviderAdmissionUnavailableError):
            with admission.acquire():
                raise AssertionError("second run entered")


def test_provider_timeout_does_not_complete_and_safe_retry_is_idempotent() -> None:
    first = run_review_with_sdk_timeout()
    assert first.reviewStatus == "failed"
    assert persisted_completed_review_count() == 0
    second = retry_with_scripted_sdk_success()
    assert second.reviewStatus == "completed"
    assert persisted_completed_review_count() == 1
    assert native_tool_call_ids_are_unique()
```

Also add named cases for retry exhaustion, malformed SDK tool call, budget
reached and request cancellation. Assert model text never creates
`verification.completed`.

- [ ] **Step 2: Run red tests**

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_llm_operations.py \
  agent-server/tests/ai4b/test_reliability.py -q
```

Expected: admission tests FAIL because the class does not exist.

- [ ] **Step 3: Implement the minimal policy guard**

Use `threading.BoundedSemaphore` and a context manager. Wrap only the existing
`handle.conversation.arun()` admission boundary. Do not create a queue, worker,
background thread, scheduler or retry loop. On admission timeout, use the
existing safe runtime-unavailable path; do not add a public response code.

SDK `LLM` owns provider retries. FocusProof whole-review retry remains the
existing explicit client request protected by Session lock and persistence.

- [ ] **Step 4: Run green concurrency and reliability tests**

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_llm_operations.py \
  agent-server/tests/ai4b/test_reliability.py \
  agent-server/tests/openhands_runtime/test_concurrent_review_lock.py \
  agent-server/tests/openhands_runtime/test_runtime_failure.py -q
```

Expected: PASS; different deterministic Sessions still run concurrently when
admission capacity permits it.

- [ ] **Step 5: Commit**

```bash
git add agent-server/focusproof/openhands_runtime/provider_admission.py \
  agent-server/focusproof/openhands_runtime/manager.py \
  agent-server/focusproof/api/app.py \
  agent-server/tests/ai4c/test_llm_operations.py \
  agent-server/tests/ai4b/test_reliability.py \
  agent-server/tests/openhands_runtime/test_concurrent_review_lock.py
git commit -m "feat: bound real provider admission and failure"
```

### Task 5: Explicit DashScope Smoke Contract

**Files:**
- Create: `agent-server/tests/ai4c/test_real_provider.py`
- Modify: `pyproject.toml`
- Modify: `agent-server/tests/openhands_runtime/test_real_llm.py`

**Interfaces:**
- Consumes: backend environment only after AI0 authorizes the invocation
- Produces: sanitized aggregate test output only

- [ ] **Step 1: Write guarded smoke tests**

```python
@pytest.mark.real_llm
def test_dashscope_smoke_uses_native_bounded_conversation(request: pytest.FixtureRequest) -> None:
    require_exact_real_llm_selection(request)
    result, usage = run_one_general_learning_review()
    assert result.usedOpenHandsConversation is True
    assert result.actionEventsCount >= 1
    assert result.observationEventsCount >= 1
    assert usage.call_count <= 4
    assert usage.output_tokens <= 1024 * 4
    assert usage.cost_usd <= 0.10
    assert usage.latency_seconds <= 60
```

Output is limited to Session ID, status, native event counts and aggregate
calls/tokens/cost/duration.

- [ ] **Step 2: Prove default selection excludes smoke and keys**

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_real_provider.py -q -m "not real_llm"
```

Expected: the real smoke is deselected and no provider config is loaded.

- [ ] **Step 3: Implement exact guard and bounds**

`require_exact_real_llm_selection()` accepts only `-m real_llm`, refuses mixed
marker expressions and validates: concurrency 1, retries 1, four calls, a
16,384-token SDK context window (not a per-call input quota), 1024 output
tokens/call, USD 0.10 total, 30-second provider timeout and 60-second review
timeout. It rejects any environment containing `ALLOW_SHORT_CONTEXT_WINDOWS`.

- [ ] **Step 4: Run deterministic green tests**

Repeat Step 2 and run:

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_llm_operations.py \
  agent-server/tests/openhands_runtime/test_real_llm.py -q -m "not real_llm"
```

Expected: deterministic tests PASS and real tests are deselected.

- [ ] **Step 5: Run real smoke only after separate AI0 authorization**

Authorized command:

```bash
FOCUSPROOF_PROFILE=staging LITELLM_LOCAL_MODEL_COST_MAP=true \
  .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_real_provider.py -q -m real_llm -s
```

AI0 authorization must name this command and USD 0.10 maximum. If authorization
or credential is absent, record `BLOCKED`; do not run or mark passed. Never echo
environment values.

- [ ] **Step 6: Commit guarded contract**

```bash
git add pyproject.toml agent-server/tests/ai4c/test_real_provider.py \
  agent-server/tests/openhands_runtime/test_real_llm.py
git commit -m "test: gate bounded real provider acceptance"
```

### Task 6: AI4C.1 Regression, Report, and Stop

**Files:**
- Create: `docs/research/AI4C1_REAL_LLM_OPERATIONS_REPORT.md`
- Verify: every AI4C.1-owned file

**Interfaces:**
- Produces: the phase evidence required by the master report template

- [ ] **Step 1: Run complete deterministic backend gate**

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  -u GOOGLE_API_KEY -u GEMINI_API_KEY -u AZURE_OPENAI_API_KEY \
  -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u LLM_API_KEY \
  .venv/bin/python -m pytest agent-server/tests -q \
  -m "not real_llm and not postgres and not staging_external"
.venv/bin/ruff check agent-server
.venv/bin/mypy agent-server
git diff --check
```

Expected: zero failures. Record exact pass/deselect/warning counts, versions and
durations.

- [ ] **Step 2: Write phase report from observed evidence**

Use every master report heading. Record the `LocalConversation` budget factory
gap and deletion condition. Record real smoke as PASS only when the separately
authorized command actually ran; otherwise record BLOCKED.

- [ ] **Step 3: Verify report and scope**

```bash
git diff --check
git status --short
git diff --name-only d93416e58298d75e64416e35d9a5b080cc7260fa...HEAD
```

Expected: only plan files and AI4C.1-owned files changed; no `.env`, `var`,
frontend, protocol, scoring, Web3 or multimodal path appears.

- [ ] **Step 4: Commit report and stop**

```bash
git add docs/research/AI4C1_REAL_LLM_OPERATIONS_REPORT.md
git commit -m "docs: report AI4C1 real LLM operations"
git diff --check
git status --short --branch
```

Expected: clean worktree. Send AI0 HEAD, commits, exact files, tests, real-smoke
status, call/token/cost evidence, reused SDK APIs, gaps and risks. Stop without
opening AI4C.2.
