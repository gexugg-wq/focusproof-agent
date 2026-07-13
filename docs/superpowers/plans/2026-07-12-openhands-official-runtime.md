# OpenHands Official Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `POST /sessions/{session_id}/review` run a real installed-SDK `LocalConversation` with native tools and native events, while FocusProof independently projects audit events and calculates the final score.

**Architecture:** A session-scoped `ConversationManager` owns `ConversationHandle` objects created by an injectable `ConversationFactory`. Production builds a real `LLM` and SDK `Agent`; tests inject SDK `TestLLM`, but both paths execute `LocalConversation.run()`, native `ActionEvent`, native `ToolExecutor`, and native `ObservationEvent`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, OpenHands SDK local path dependency, pytest, Ruff, Mypy.

## Global Constraints

- Work only in `/home/holy/web3/focusproof-agent`.
- Do not modify `frontend/`, `contracts/`, `docs/architecture/`, `docs/protocol/`, or `docs/project-management/`.
- Do not expose API keys, authorization headers, or complete environment-variable dumps.
- Production review cannot use deterministic or scripted fallback.
- Scripted tests must report `conversationMode="openhands-local-scripted-test"`.
- `usedOpenHandsConversation=true` requires a successful SDK `LocalConversation.run()`.
- FocusProof scoring remains independent of LLM and tool-provided score fields.
- This directory has no `.git`; commit steps are replaced by verified file checkpoints.

---

### Task 1: Runtime Result And Handle Models

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/__init__.py`
- Create: `agent-server/focusproof/openhands_runtime/handle.py`
- Test: `agent-server/tests/openhands_runtime/test_factory.py`

**Interfaces:**
- Produces: `RuntimeMode`, `ReviewStatus`, `ConversationHandle`, `RuntimeReviewResult`.
- `ConversationHandle.conversation` is typed as SDK `LocalConversation` and stores a UUID `conversation_id`.

- [ ] **Step 1: Write the failing model/import test**

```python
from uuid import UUID

from focusproof.openhands_runtime.handle import ConversationHandle


def test_conversation_handle_requires_sdk_uuid() -> None:
    assert ConversationHandle.model_fields["conversation_id"].annotation is UUID
```

- [ ] **Step 2: Verify RED**

Run: `pytest agent-server/tests/openhands_runtime/test_factory.py -v`

Expected: collection fails because `focusproof.openhands_runtime` does not exist.

- [ ] **Step 3: Implement the models**

```python
RuntimeMode = Literal[
    "openhands-local-real",
    "openhands-local-scripted-test",
    "unavailable",
    "failed",
]
ReviewStatus = Literal["completed", "awaiting_user", "failed"]

class ConversationHandle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    session_id: str
    conversation: LocalConversation
    conversation_id: UUID
    workspace_path: Path
    persistence_path: Path
    runtime_mode: RuntimeMode
    projected_event_ids: set[str] = Field(default_factory=set)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

class RuntimeReviewResult(BaseModel):
    sessionId: str
    conversationMode: RuntimeMode
    usedOpenHandsConversation: bool
    conversationId: str | None
    nativeEventCount: int
    messageEventsCount: int
    actionEventsCount: int
    observationEventsCount: int
    projectedEventsCount: int
    reviewStatus: ReviewStatus
    agentQuestions: list[dict[str, str]] = Field(default_factory=list)
    reviewResult: ReviewResult | None = None
    error: str | None = None
```

- [ ] **Step 4: Verify GREEN**

Run: `pytest agent-server/tests/openhands_runtime/test_factory.py -v`

Expected: model test passes.

- [ ] **Step 5: Record checkpoint**

Run: `find agent-server/focusproof/openhands_runtime -maxdepth 1 -type f -print`

Expected: `__init__.py` and `handle.py` are present only in the WSL project.

### Task 2: Native Read-Only FocusProof Tools

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/tools/__init__.py`
- Create: `agent-server/focusproof/openhands_runtime/tools/evidence_verification.py`
- Create: `agent-server/focusproof/openhands_runtime/tools/learner_input.py`
- Create: `agent-server/focusproof/openhands_runtime/tools/review_draft.py`
- Test: `agent-server/tests/openhands_runtime/test_tool_execution.py`

**Interfaces:**
- Consumes: `Evidence` and a `SessionEvidenceRepository.get_evidence(session_id, evidence_id)` protocol.
- Produces: SDK-native Action, Observation, ToolExecutor, and ToolDefinition classes for all three tools.

- [ ] **Step 1: Write failing native-type and repository-trust tests**

```python
def test_verification_executor_loads_authoritative_evidence_by_id(repository) -> None:
    executor = EvidenceVerificationExecutor(repository, "sess_1")
    observation = executor(EvidenceVerificationAction(evidence_id="ev_1"))
    assert observation.evidence_id == "ev_1"
    assert observation.findings == ["authoritative repository text"]
    assert repository.requested == [("sess_1", "ev_1")]

def test_actions_and_observations_are_sdk_native() -> None:
    assert issubclass(EvidenceVerificationAction, OpenHandsAction)
    assert issubclass(EvidenceVerificationObservation, OpenHandsObservation)
```

- [ ] **Step 2: Verify RED**

Run: `pytest agent-server/tests/openhands_runtime/test_tool_execution.py -v`

Expected: imports fail because native tools do not exist.

- [ ] **Step 3: Implement exact tool contracts**

```python
class EvidenceVerificationAction(Action):
    evidence_id: str

class EvidenceVerificationObservation(Observation):
    evidence_id: str
    verified: bool
    evidence_type: str
    findings: list[str]
    weak_signals: list[str]
    source_refs: list[str]
    verifier: str

class LearnerInputAction(Action):
    question: str
    reason: str
    requested_evidence_type: str

class LearnerInputObservation(Observation):
    question_id: str
    status: Literal["awaiting_user"] = "awaiting_user"
    question: str
    reason: str

class ReviewDraftAction(Action):
    credibility_findings: list[str]
    understanding_findings: list[str]
    contradictions: list[str]
    recommended_next_step: str
    confidence: float = Field(ge=0.0, le=1.0)

class ReviewDraftObservation(Observation):
    accepted: bool = True
    draft_id: str
    credibility_findings: list[str]
    understanding_findings: list[str]
    contradictions: list[str]
    recommended_next_step: str
    confidence: float
```

Each definition uses `ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)` and returns an executor-bound `ToolDefinition` sequence.

- [ ] **Step 4: Verify GREEN and type checking**

Run: `pytest agent-server/tests/openhands_runtime/test_tool_execution.py -v`

Run: `mypy agent-server/focusproof/openhands_runtime/tools`

Expected: tests pass and Mypy reports no issues.

### Task 3: Factory, Prompt, And Tool Safety

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/prompts.py`
- Create: `agent-server/focusproof/openhands_runtime/factory.py`
- Modify: `agent-server/focusproof/openhands_adapter/llm_config.py`
- Test: `agent-server/tests/openhands_runtime/test_factory.py`

**Interfaces:**
- Consumes: `build_openhands_llm_config()`, repository, audit EventLog, and optional injected LLM provider.
- Produces: `ConversationFactory.create(session_id, goal) -> ConversationHandle`.

- [ ] **Step 1: Add failing factory and safety tests**

```python
def test_factory_creates_real_local_conversation(scripted_factory) -> None:
    handle = scripted_factory.create("sess_1", learning_goal())
    assert isinstance(handle.conversation, LocalConversation)
    assert handle.runtime_mode == "openhands-local-scripted-test"
    assert handle.conversation_id == uuid5(NAMESPACE_URL, "focusproof:sess_1")

def test_initialized_agent_contains_only_focusproof_tools(scripted_factory) -> None:
    handle = scripted_factory.create("sess_2", learning_goal())
    handle.conversation.send_message("initialize")
    names = set(handle.conversation.agent.tools_map)
    assert names == {
        "focusproof_evidence_verification",
        "focusproof_learner_input",
        "focusproof_review_draft",
    }
    assert not names & {"terminal", "file_editor", "browser", "apply_patch"}
```

- [ ] **Step 2: Verify RED**

Run: `pytest agent-server/tests/openhands_runtime/test_factory.py -v`

Expected: fails because `ConversationFactory` is missing.

- [ ] **Step 3: Implement factory construction**

```python
conversation_id = uuid5(NAMESPACE_URL, f"focusproof:{session_id}")
agent = Agent(
    llm=llm_provider.create(session_id),
    tools=registered_focusproof_tool_specs(session_id, repository),
    include_default_tools=[],
    system_prompt=FOCUSPROOF_SYSTEM_PROMPT,
)
conversation = Conversation(
    agent=agent,
    workspace=workspace_path,
    persistence_dir=persistence_path,
    conversation_id=conversation_id,
    callbacks=[projector.on_event],
    max_iteration_per_run=6,
    visualizer=None,
    delete_on_close=False,
    tags={"application": "focusproof", "sessionid": session_id},
)
if not isinstance(conversation, LocalConversation):
    raise RuntimeCreationError("SDK did not create LocalConversation")
```

Production LLM construction uses the existing sanitized config loader. Test construction uses `TestLLM.from_messages(...)` and sets runtime mode only inside the injected provider.

- [ ] **Step 4: Verify GREEN**

Run: `pytest agent-server/tests/openhands_runtime/test_factory.py -v`

Expected: both LocalConversation and actual initialized-tool assertions pass.

### Task 4: Native Event Projection And Idempotence

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/projector.py`
- Modify: `agent-server/focusproof/runtime/event_log.py`
- Test: `agent-server/tests/openhands_runtime/test_event_projection.py`

**Interfaces:**
- Consumes: SDK Event subclasses, conversation UUID, FocusProof `InMemoryEventLog`.
- Produces: `OpenHandsEventProjector.on_event(event)` and `reconcile(events) -> int`.

- [ ] **Step 1: Write failing traceability and deduplication tests**

```python
def test_projection_preserves_native_identity(projector, native_action) -> None:
    projector.on_event(native_action)
    event = projector.audit_log.latest("sess_1")
    assert event.payload["sourceRuntime"] == "openhands-local"
    assert event.payload["sourceOpenHandsEventId"] == native_action.id
    assert event.payload["sourceOpenHandsEventType"] == "ActionEvent"
    assert event.payload["sourceToolCallId"] == native_action.tool_call_id

def test_reconcile_is_idempotent(projector, native_events) -> None:
    assert projector.reconcile(native_events) == len(native_events)
    count = projector.audit_log.count("sess_1")
    assert projector.reconcile(native_events) == 0
    assert projector.audit_log.count("sess_1") == count
```

- [ ] **Step 2: Verify RED**

Run: `pytest agent-server/tests/openhands_runtime/test_event_projection.py -v`

Expected: fails because projector and native-ID deduplication are absent.

- [ ] **Step 3: Implement projector mappings**

Map native events as follows:

```text
MessageEvent(goal) -> goal.submitted
MessageEvent(evidence) -> evidence.submitted
MessageEvent(answer) -> answer.submitted
ActionEvent(evidence verification) -> verification.requested
ActionEvent(learner input) -> question.asked
ObservationEvent(evidence verification) -> verification.completed
ConversationErrorEvent -> error.occurred
```

Use `event.id` as the deduplication key and native EventLog order as `sourceOpenHandsEventIndex`. Add `has_source_event(session_id, source_event_id)` to `InMemoryEventLog` so callback and reconciliation share one authoritative check.

- [ ] **Step 4: Verify GREEN**

Run: `pytest agent-server/tests/openhands_runtime/test_event_projection.py agent-server/tests/runtime/test_event_log.py -v`

Expected: traceability and repeated reconciliation pass without duplicate product events.

### Task 5: Conversation Lifecycle And Native Event Flow

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/manager.py`
- Test: `agent-server/tests/openhands_runtime/test_conversation_lifecycle.py`
- Test: `agent-server/tests/openhands_runtime/test_native_event_flow.py`

**Interfaces:**
- Consumes: `ConversationFactory`, session repository, and projector.
- Produces: the six required `ConversationManager` methods.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_manager_reuses_same_conversation(manager) -> None:
    first = manager.create("sess_1", learning_goal())
    manager.send_evidence("sess_1", evidence("ev_1"))
    second = manager.get("sess_1")
    assert second.conversation is first.conversation
    assert second.conversation_id == first.conversation_id

def test_runtime_paths_are_session_scoped(manager) -> None:
    handle = manager.create("sess_2", learning_goal())
    assert handle.workspace_path.as_posix().endswith("var/conversations/sess_2/workspace")
    assert handle.persistence_path.as_posix().endswith("var/conversations/sess_2/persistence")
```

- [ ] **Step 2: Write failing native-flow test with SDK TestLLM**

Script responses are: verification tool call, review-draft tool call, final message. Then assert:

```python
events = list(handle.conversation.state.events)
action = next(e for e in events if isinstance(e, ActionEvent))
observation = next(e for e in events if isinstance(e, ObservationEvent))
assert action.tool_call_id == observation.tool_call_id
assert events.index(action) < events.index(observation)
assert any(isinstance(e, MessageEvent) for e in events)
```

- [ ] **Step 3: Verify RED**

Run: `pytest agent-server/tests/openhands_runtime/test_conversation_lifecycle.py agent-server/tests/openhands_runtime/test_native_event_flow.py -v`

Expected: fails because manager does not exist.

- [ ] **Step 4: Implement manager message envelopes and run**

Use `json.dumps(..., sort_keys=True)` envelopes with `kind`, `session_id`, and IDs. `run_review()` must call exactly:

```python
handle.conversation.run()
native_events = list(handle.conversation.state.events)
projected = handle.projector.reconcile(native_events)
return extractor.extract(handle, native_events, projected)
```

There is no custom `for range` agent-step loop.

- [ ] **Step 5: Verify GREEN**

Run: `pytest agent-server/tests/openhands_runtime/test_conversation_lifecycle.py agent-server/tests/openhands_runtime/test_native_event_flow.py -v`

Expected: the same LocalConversation is reused and native action/observation order passes.

### Task 6: Awaiting User And Independent Scoring

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/result_extractor.py`
- Modify: `agent-server/focusproof/domain/scoring.py`
- Test: `agent-server/tests/openhands_runtime/test_runtime_failure.py`
- Test: `agent-server/tests/openhands_runtime/test_conversation_lifecycle.py`

**Interfaces:**
- Consumes: native events, repository evidence, answers, and converted verification observations.
- Produces: completed, awaiting-user, or failed `RuntimeReviewResult`.

- [ ] **Step 1: Write failing no-premature-score test**

```python
def test_learner_input_stops_before_scoring(manager, audit_log) -> None:
    result = manager.run_review("sess_wait")
    assert result.reviewStatus == "awaiting_user"
    assert result.reviewResult is None
    assert result.agentQuestions
    assert not audit_log.get_by_type("sess_wait", "score.calculated")
    assert not audit_log.get_by_type("sess_wait", "review.completed")
```

- [ ] **Step 2: Write failing score-ownership test**

Create a draft action without a score field, execute it, and assert the final numeric score equals `score_learning_session(evidence, answers, observations)`. Also assert no draft model accepts a `score` input.

- [ ] **Step 3: Verify RED**

Run: `pytest agent-server/tests/openhands_runtime/test_conversation_lifecycle.py -v`

Expected: extractor behavior is missing.

- [ ] **Step 4: Implement extraction rules**

Search native observations after the latest user answer. A learner-input observation wins over any later plain agent text and yields `awaiting_user`. An accepted draft yields scoring and appends `score.calculated` followed by `review.completed`. No draft and no question yields `failed`.

- [ ] **Step 5: Verify GREEN**

Run: `pytest agent-server/tests/openhands_runtime/test_conversation_lifecycle.py agent-server/tests/domain/test_scoring.py -v`

Expected: waiting never scores, and completed review score comes only from FocusProof.

### Task 7: Formal API And Structured 503

**Files:**
- Create: `agent-server/focusproof/api/models.py`
- Modify: `agent-server/focusproof/api/app.py`
- Modify: `agent-server/tests/api/test_review_conversation_runtime.py`
- Test: `agent-server/tests/openhands_runtime/test_runtime_failure.py`

**Interfaces:**
- Consumes: singleton production manager and session repository adapter.
- Produces: formal review response fields from `RuntimeReviewResult`; no `useRealLlm` request switch.

- [ ] **Step 1: Replace fake-mode API assertions with failing official-runtime assertions**

```python
assert data["conversationMode"] == "openhands-local-scripted-test"
assert data["usedOpenHandsConversation"] is True
assert data["conversationId"]
assert data["nativeEventCount"] >= 4
assert data["actionEventsCount"] >= 1
assert data["observationEventsCount"] >= 1
assert data["reviewStatus"] == "completed"
```

Override the app's manager dependency with the TestLLM-backed manager. Do not add a public JSON flag for test mode.

- [ ] **Step 2: Add failing missing-key and run-error tests**

```python
response = client.post(f"/sessions/{session_id}/review")
assert response.status_code == 503
assert response.json()["usedOpenHandsConversation"] is False
assert response.json()["conversationMode"] in {"unavailable", "failed"}
assert response.json()["reviewResult"] is None
```

- [ ] **Step 3: Verify RED**

Run: `pytest agent-server/tests/api/test_review_conversation_runtime.py agent-server/tests/openhands_runtime/test_runtime_failure.py -v`

Expected: current API returns `openhands-fake` with HTTP 200, so assertions fail.

- [ ] **Step 4: Rewire the API**

Remove `ReviewSessionRequest.useRealLlm`. `POST /sessions` creates the product record and manager handle when runtime configuration is available. Evidence and answer endpoints store product state before forwarding IDs to the manager. Review delegates only to `ConversationManager.run_review()` and returns `JSONResponse(status_code=503, content=result.model_dump())` for unavailable/failed results.

- [ ] **Step 5: Verify GREEN**

Run: `pytest agent-server/tests/api/test_review_conversation_runtime.py agent-server/tests/api/test_api_sessions.py agent-server/tests/openhands_runtime/test_runtime_failure.py -v`

Expected: injected scripted mode is truthful; unavailable production returns structured 503 without fallback.

### Task 8: Legacy Downgrade And Debug Reuse

**Files:**
- Modify: `agent-server/focusproof/openhands_adapter/learning_conversation.py`
- Modify: `agent-server/focusproof/openhands_adapter/real_conversation.py`
- Modify: `agent-server/focusproof/api/app.py`
- Modify: `agent-server/tests/api/test_openhands_debug_api.py`

**Interfaces:**
- Consumes: production `ConversationFactory`.
- Produces: debug endpoint using the same factory; deterministic class remains isolated from formal API.

- [ ] **Step 1: Add failing import-boundary tests**

Read `focusproof.api.app` source and assert it contains no `DeterministicLearningAgentFallback`, `FocusProofLearningConversation`, `openhands-fake`, or `useRealLlm` reference. Assert debug endpoint calls a factory-backed debug service.

- [ ] **Step 2: Verify RED**

Run: `pytest agent-server/tests/api/test_openhands_debug_api.py agent-server/tests/api/test_review_conversation_runtime.py -v`

Expected: current formal route imports `FocusProofLearningConversation` and exposes `useRealLlm`.

- [ ] **Step 3: Remove formal-path legacy usage**

Keep deterministic fallback only for isolated compatibility tests. Rename any surviving fake mode to `projection-fallback`. Move reusable prompt/extraction behavior from `real_conversation.py` into factory/prompts/result extractor, and have debug API call that runtime service.

- [ ] **Step 4: Verify GREEN and search invariant**

Run: `grep -R -nE 'FocusProofLearningConversation|DeterministicLearningAgentFallback|openhands-fake|useRealLlm' agent-server/focusproof/api agent-server/focusproof/openhands_runtime`

Expected: no output.

### Task 9: Real LLM Test, Report, And Full Verification

**Files:**
- Create: `agent-server/tests/openhands_runtime/test_real_llm.py`
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `docs/research/OPENHANDS_CONVERSATION_OFFICIAL_RUNTIME.md`

**Interfaces:**
- Produces: `real_llm` marker, `var/` ignore rule, sanitized delivery evidence.

- [ ] **Step 1: Add real-LLM test with safe skip**

```python
@pytest.mark.real_llm
def test_real_llm_runs_local_conversation_and_native_tool_flow() -> None:
    if not get_llm_config_status()["canBuildConfig"]:
        pytest.skip("real LLM configuration unavailable")
    result = production_manager().run_review(real_fixture_session())
    assert result.conversationMode == "openhands-local-real"
    assert result.usedOpenHandsConversation is True
    assert result.actionEventsCount >= 1
    assert result.observationEventsCount >= 1
```

Register the marker in `pyproject.toml`; never print config values or headers.

- [ ] **Step 2: Add `var/` to `.gitignore` and clean generated caches**

The ignore entry is exactly `var/`. Remove only generated `__pycache__`, `.pytest_cache`, `.mypy_cache`, and `.ruff_cache` directories inside the WSL project after final verification.

- [ ] **Step 3: Run required automated checks**

Run:

```bash
pytest agent-server/tests -v
pytest agent-server/tests -m "not real_llm" -v
ruff check agent-server
mypy agent-server
```

Expected: all non-real tests pass; Ruff and Mypy report no issues.

- [ ] **Step 4: Run explicit real integration**

Run: `pytest agent-server/tests -m real_llm -v -s`

Expected: pass when `.env` is configured, otherwise one explicit skip. No secret appears in output.

- [ ] **Step 5: Run API end-to-end verification**

Start FastAPI locally, then call `POST /sessions`, `POST /sessions/{id}/evidence`, `POST /sessions/{id}/review`, and `GET /sessions/{id}/events`. Verify the response is `openhands-local-real` and audit records contain native action and observation IDs matching entries in `conversation.state.events`.

- [ ] **Step 6: Write delivery report**

Record changed files, official call chain, lifecycle, native SDK classes, tool schemas, sanitized native/projected event samples, exact test outputs, real LLM result, limitations, protocol-file status, forbidden-area status, and the evidence-based AI3 readiness verdict.

- [ ] **Step 7: Completion audit**

Check every numbered requirement in the source goal against a test, command output, runtime response, or report section. Do not mark complete while any requirement lacks direct evidence.
