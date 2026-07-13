# OpenHands Conversation Official Runtime

## 1. Outcome

The official `POST /sessions/{session_id}/review` path now runs the locally installed OpenHands SDK `LocalConversation`. It no longer imports or calls `FocusProofLearningConversation`, `DeterministicLearningAgentFallback`, or an `openhands-fake` mode. Production failure is explicit and never falls back to deterministic review.

The old compatibility adapter is retained only for isolated projection tests. It is truthfully named `projection-fallback` and always reports `usedOpenHandsConversation=false`.

## 2. Files Created

Runtime:

- `agent-server/focusproof/openhands_runtime/__init__.py`
- `agent-server/focusproof/openhands_runtime/handle.py`
- `agent-server/focusproof/openhands_runtime/factory.py`
- `agent-server/focusproof/openhands_runtime/manager.py`
- `agent-server/focusproof/openhands_runtime/prompts.py`
- `agent-server/focusproof/openhands_runtime/projector.py`
- `agent-server/focusproof/openhands_runtime/result_extractor.py`
- `agent-server/focusproof/openhands_runtime/tools/__init__.py`
- `agent-server/focusproof/openhands_runtime/tools/evidence_verification.py`
- `agent-server/focusproof/openhands_runtime/tools/learner_input.py`
- `agent-server/focusproof/openhands_runtime/tools/review_draft.py`
- `agent-server/focusproof/api/models.py`

Tests:

- `agent-server/tests/openhands_runtime/__init__.py`
- `agent-server/tests/openhands_runtime/conftest.py`
- `agent-server/tests/openhands_runtime/test_factory.py`
- `agent-server/tests/openhands_runtime/test_conversation_lifecycle.py`
- `agent-server/tests/openhands_runtime/test_native_event_flow.py`
- `agent-server/tests/openhands_runtime/test_tool_execution.py`
- `agent-server/tests/openhands_runtime/test_event_projection.py`
- `agent-server/tests/openhands_runtime/test_runtime_failure.py`
- `agent-server/tests/openhands_runtime/test_real_llm.py`

Design records:

- `docs/superpowers/specs/2026-07-12-openhands-official-runtime-design.md`
- `docs/superpowers/plans/2026-07-12-openhands-official-runtime.md`
- `docs/research/OPENHANDS_CONVERSATION_OFFICIAL_RUNTIME.md`

## 3. Files Modified

- `agent-server/focusproof/api/app.py`
- `agent-server/focusproof/openhands_adapter/agent.py`
- `agent-server/focusproof/openhands_adapter/learning_conversation.py`
- `agent-server/focusproof/openhands_adapter/real_conversation.py`
- `agent-server/focusproof/runtime/event_log.py`
- `agent-server/tests/api/test_api_sessions.py`
- `agent-server/tests/api/test_review_conversation_runtime.py`
- `agent-server/tests/openhands_adapter/test_event_projection.py`
- `agent-server/tests/openhands_adapter/test_learning_conversation.py`
- `agent-server/tests/openhands_adapter/test_real_conversation_safety.py`
- `.gitignore`
- `pyproject.toml`

No files were changed in `frontend/`, `contracts/`, `docs/architecture/`, `docs/protocol/`, or `docs/project-management/`.

## 4. Official Call Chain

```text
FastAPI POST /sessions/{session_id}/review
-> ConversationManager.get/create
-> ConversationFactory
-> installed OpenHands LLM
-> installed OpenHands Agent with only three FocusProof tools
-> Conversation factory
-> LocalConversation
-> conversation.send_message(goal/evidence/answer envelopes)
-> LocalConversation.run()
-> Agent.step()
-> native ActionEvent
-> native ToolDefinition / ToolExecutor
-> native ObservationEvent
-> ConversationState.events
-> callback projection plus post-run reconciliation
-> RuntimeResultExtractor
-> score_learning_session(evidence, answers, observation facts)
-> FocusProof score.calculated / review.completed audit events
```

There is no route-owned `for/range` agent-step loop and no deterministic fallback in this chain.

## 5. Conversation Lifecycle

`ConversationManager` owns one `ConversationHandle` per FocusProof session. The handle stores:

- FocusProof session ID;
- actual SDK `LocalConversation`;
- SDK conversation UUID;
- workspace and persistence paths;
- runtime mode;
- projected native event IDs;
- creation timestamp.

The installed SDK requires `ConversationID = uuid.UUID`; passing a `sess_...` string fails because SDK persistence accesses `.hex`. The factory therefore derives a stable UUID with `uuid5(NAMESPACE_URL, "focusproof:{session_id}")` and returns that UUID in the API.

Runtime paths are:

```text
var/conversations/{session_id}/workspace/
var/conversations/{session_id}/persistence/
```

Repeated evidence, answers, and reviews reuse the same handle. A real API acceptance run proved that an awaiting-user review and its answered continuation retained the same SDK conversation ID while native event count increased from 7 to 10.

## 6. Native OpenHands Classes Used

- `LLM`
- `Agent`
- `Conversation`
- `LocalConversation`
- `ConversationState`
- SDK `EventLog` through `conversation.state.events`
- `MessageEvent`
- `ActionEvent`
- `ObservationEvent`
- `ConversationErrorEvent`
- `FinishAction`
- `Action`
- `Observation`
- `Tool`
- `ToolDefinition`
- `ToolExecutor`
- `ToolAnnotations`
- `register_tool`
- `TestLLM` for scripted tests only

Runtime mode is derived from the actual LLM object. SDK `TestLLM` produces `openhands-local-scripted-test`; the production provider produces `openhands-local-real`. There is no request field that selects either mode.

## 7. Custom Tool Definitions

### FocusProofEvidenceVerificationTool

Input:

```json
{"evidence_id":"ev_..."}
```

The executor uses the ID to fetch authoritative evidence from the FocusProof session repository. It cannot receive or trust an LLM-provided evidence body.

Output fields:

- `evidence_id`
- `verified`
- `evidence_type`
- `findings`
- `weak_signals`
- `source_refs`
- `verifier`

### FocusProofLearnerInputTool

Input fields are `question`, `reason`, and `requested_evidence_type`. Output fields are `question_id`, `status="awaiting_user"`, `question`, and `reason`. The runtime pauses after this native observation and does not score.

### FocusProofReviewDraftTool

Input fields are credibility findings, understanding findings, contradictions, recommended next step, and confidence. The output accepts and normalizes the draft. Neither action nor observation has a score field.

All three tools declare read-only, non-destructive, idempotent, closed-world annotations. The initialized agent's real `tools_map` is tested to contain exactly:

```text
focusproof_evidence_verification
focusproof_learner_input
focusproof_review_draft
```

Terminal, file editor, browser, workspace mutation, and patch tools are absent.

## 8. Native Event Sample

Sanitized real API run:

```json
{
  "action": {
    "type": "ActionEvent",
    "id": "ada3d224-4a9b-4bee-8261-61de421a56cf",
    "tool": "focusproof_evidence_verification",
    "tool_call_id": "call_645d68544e8c4c3e97367854"
  },
  "observation": {
    "type": "ObservationEvent",
    "id": "5cdfac37-cee4-4148-ba6f-617e0e1fdcf0",
    "tool": "focusproof_evidence_verification",
    "tool_call_id": "call_645d68544e8c4c3e97367854"
  }
}
```

The matching tool call IDs and event indexes prove action-before-observation ordering in the native SDK EventLog.

## 9. FocusProof Projection Sample

```json
{
  "type": "verification.completed",
  "payload": {
    "sourceRuntime": "openhands-local",
    "sourceConversationId": "6d35231b-c55d-59fe-9da3-b2962b4f19e1",
    "sourceOpenHandsEventId": "5cdfac37-cee4-4148-ba6f-617e0e1fdcf0",
    "sourceOpenHandsEventType": "ObservationEvent",
    "sourceOpenHandsEventIndex": 4,
    "sourceToolCallId": "call_645d68544e8c4c3e97367854",
    "relatedEvidenceIds": ["ev_..."],
    "sessionId": "sess_..."
  }
}
```

SDK callbacks project events in real time. After every run, the projector iterates `conversation.state.events` and adds only missing native IDs. Duplicate reconciliation is covered by a test.

`FinishAction` is explicitly recognized as a native terminal action and maps to `session.ended`. It is not enabled in the current three-tool review Agent, but persisted or future native finish events are understood.

## 10. API Behavior

Success or awaiting-user responses include:

- `conversationMode`
- `usedOpenHandsConversation`
- `conversationId`
- native message/action/observation counts
- projected event count
- `reviewStatus`
- questions or review result
- sanitized error field

Missing credentials return structured HTTP 503 with `conversationMode="unavailable"` and `usedOpenHandsConversation=false`. SDK creation/run/extraction failure returns structured HTTP 503 with `conversationMode="failed"`. Neither path runs deterministic scoring.

The public OpenAPI operation for review has no request body, so callers cannot enable scripted mode.

## 11. Verification Results

Default suite:

```text
pytest agent-server/tests -v
59 passed, 1 skipped, 1 upstream deprecation warning
```

Non-real suite:

```text
pytest agent-server/tests -m "not real_llm" -v
59 passed, 1 deselected, 1 upstream deprecation warning
```

Static checks:

```text
ruff check agent-server
All checks passed!

mypy agent-server
Success: no issues found in 70 source files
```

Explicit real integration:

```text
pytest agent-server/tests -m real_llm -v -s
1 passed, 59 deselected
```

The real integration used the configured model without printing credentials. LiteLLM emitted only a model-cost metadata warning because that model is not in its price table.

## 12. Real FastAPI Acceptance

First production-mode review:

```text
conversationMode=openhands-local-real
usedOpenHandsConversation=true
reviewStatus=awaiting_user
conversationId=6d35231b-c55d-59fe-9da3-b2962b4f19e1
nativeEventCount=7
actionEventsCount=2
observationEventsCount=2
```

After submitting the requested learner answer and reviewing again:

```text
conversationMode=openhands-local-real
usedOpenHandsConversation=true
reviewStatus=completed
conversationId=6d35231b-c55d-59fe-9da3-b2962b4f19e1
nativeEventCount=10
actionEventsCount=3
observationEventsCount=3
FocusProof score=77
error=null
```

The unchanged conversation ID proves session-level reuse. The first run did not emit `review.completed`; scoring occurred only after the learner answer.

## 13. Current Limitations

- Manager ownership is process-local. SDK events are file-backed, but the current product session repository is in memory, so complete application recovery after a process restart still requires durable product-session storage.
- Multi-process conversation locking is not implemented.
- `RemoteConversation`, WebSocket streaming, and Agent Server remote mode are outside this task.
- Evidence verification is deterministic and repository-backed but does not yet perform real Web3 RPC, URL retrieval, OCR, or other domain-plugin integrations.
- The existing FastAPI/TestClient dependency emits one upstream Starlette/httpx deprecation warning.
- The configured model is absent from LiteLLM's cost table, so real tests emit a cost-calculation metadata warning; runtime behavior is unaffected.
- The project directory has no `.git`, so design and implementation checkpoints could not be committed.

## 14. Architecture And Protocol Status

No architecture, protocol, or project-management control document was changed. One SDK-version difference is recorded here for AI0: the installed SDK requires UUID conversation IDs, while the task pseudocode showed `conversation_id=session_id`.

## 15. AI3 Readiness

All backend phase-gate conditions for the official OpenHands local review runtime are satisfied:

- official review uses real `LocalConversation.run()`;
- native message/action/observation events are present and traceable;
- native ToolExecutor performs verification;
- fallback behavior is truthful and absent from production review;
- awaiting-user and failure states are explicit;
- real LLM and real FastAPI acceptance both passed.

AI3 may proceed, subject to AI0's controller review. The limitations above remain backend follow-up work rather than blockers for the current frontend phase gate.
