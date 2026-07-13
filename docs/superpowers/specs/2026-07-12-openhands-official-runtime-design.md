# OpenHands Official Runtime Design

## Goal

Replace the official FocusProof review path's deterministic projection loop with a real OpenHands SDK `LocalConversation` runtime. Production review must use the installed SDK's `Agent.step()`, native tool dispatch, native `ActionEvent` and `ObservationEvent`, and `ConversationState.events`. FocusProof continues to own evidence models, product audit events, review status, and final scoring.

## Scope And Constraints

- Work only in `/home/holy/web3/focusproof-agent`.
- Do not modify `frontend/`, `contracts/`, `docs/architecture/`, `docs/protocol/`, or `docs/project-management/`.
- The public review endpoint defaults to a real OpenHands local runtime.
- Scripted behavior is available only through dependency injection in tests and is named `openhands-local-scripted-test`.
- A request parameter cannot select scripted or fallback behavior.
- Missing credentials or runtime failure produces a structured HTTP 503 response. Production never silently falls back to deterministic review.
- Unit tests must not consume a real API key. A separately marked `real_llm` test is skipped by default.

## Chosen Architecture

Use a process-local `ConversationManager` backed by a `ConversationFactory`.

`ConversationFactory` constructs the installed SDK's `LLM`, `Agent`, custom tool definitions, and `Conversation`. A local workspace causes the SDK factory to return `LocalConversation`. The factory supports an injected LLM provider for tests; the concrete provider determines runtime mode. The public API cannot choose the provider.

`ConversationManager` owns one `ConversationHandle` per FocusProof session. Repeated evidence, answers, and reviews reuse the same handle. The SDK conversation ID is a deterministic UUID derived from the FocusProof session ID because the installed SDK requires `uuid.UUID`, not an arbitrary string. Persistence uses that stable UUID so a closed handle can be restored without creating an unrelated conversation.

The runtime directories are:

```text
var/conversations/{session_id}/workspace/
var/conversations/{session_id}/persistence/
```

`ConversationHandle` stores the FocusProof session ID, the actual `LocalConversation`, UUID conversation ID, paths, runtime mode, projected native event IDs, and creation timestamp.

## Components

### Factory And Handle

`openhands_runtime/factory.py` creates production and injected-test conversations. It validates the concrete returned object with `isinstance(conversation, LocalConversation)`, supplies only the three FocusProof tools, disables default tools, sets `max_iteration_per_run=6`, disables the visualizer, keeps persistence on close, and applies FocusProof tags.

`openhands_runtime/handle.py` defines `ConversationHandle` and the runtime result models. `usedOpenHandsConversation` is derived from a successful `LocalConversation.run()` result; callers cannot set it independently.

### Manager

`openhands_runtime/manager.py` implements:

```python
create(session_id, goal) -> ConversationHandle
get(session_id) -> ConversationHandle
send_evidence(session_id, evidence) -> None
send_answer(session_id, question_id, answer) -> None
run_review(session_id) -> RuntimeReviewResult
close(session_id) -> None
```

Goal, evidence, and answer inputs are sent through `conversation.send_message()` as compact JSON envelopes with a FocusProof message kind. Evidence text is included only as user context; verification tools still resolve authoritative evidence by ID from the session repository.

### Native Tools

Three read-only SDK `ToolDefinition` implementations are registered:

- `FocusProofEvidenceVerificationTool`: accepts only `evidence_id`; its executor fetches the evidence from the FocusProof session repository and returns evidence type, verification result, findings, weak signals, source references, and verifier name.
- `FocusProofLearnerInputTool`: records a structured question and returns `status="awaiting_user"` with a generated question ID.
- `FocusProofReviewDraftTool`: accepts structured findings, contradictions, next step, and confidence; it normalizes and accepts a draft but has no score field.

All actions and observations subclass the installed SDK's native `Action` and `Observation`. Execution is performed by OpenHands `Agent` dispatch through each tool's native `ToolExecutor`. No terminal, file editor, browser, workspace mutation, or patch tool is present in the initialized agent.

### Prompts And Result Extraction

The system prompt requires the agent to verify evidence by ID, request learner input when facts are insufficient, and submit a review draft when enough facts exist. It explicitly forbids assigning a final score.

`result_extractor.py` reads native events and builds one of two outcomes:

- `awaiting_user`: learner-input observation exists after the latest answer; return questions and do not score.
- `completed`: an accepted review-draft observation exists; convert evidence-verification observations into FocusProof observation facts, then call `score_learning_session()`.

The scorer consumes repository evidence, submitted answers, and normalized observation facts. LLM draft fields provide review context but cannot write or override the final score.

### Native Event Projection

`projector.py` is both the SDK callback and the post-run reconciler. It recognizes native `MessageEvent`, `ActionEvent`, `ObservationEvent`, `ConversationErrorEvent`, and finish-related events. Each projected FocusProof event includes:

```text
sourceRuntime="openhands-local"
sourceConversationId
sourceOpenHandsEventId
sourceOpenHandsEventType
sourceOpenHandsEventIndex
sourceToolCallId (when applicable)
relatedEvidenceIds
sessionId
```

Projection is idempotent by `sourceOpenHandsEventId`. The callback projects events as they happen. After `run()`, reconciliation iterates `conversation.state.events` and projects anything missing with authoritative native indexes. The FocusProof EventLog remains the product audit projection and never substitutes for the SDK EventLog.

## API Flow

```text
POST /sessions
  -> create product session
  -> ConversationManager.create
  -> LocalConversation.send_message(goal)

POST /sessions/{id}/evidence
  -> store evidence in session repository
  -> manager.send_evidence
  -> LocalConversation.send_message(evidence envelope)

POST /sessions/{id}/answer
  -> store answer
  -> manager.send_answer
  -> LocalConversation.send_message(answer envelope)

POST /sessions/{id}/review
  -> manager.run_review
  -> LocalConversation.run
  -> Agent.step
  -> native ActionEvent
  -> native ToolExecutor
  -> native ObservationEvent
  -> projector reconciliation from conversation.state.events
  -> result extraction
  -> FocusProof scoring only when review is complete
```

A successful response includes mode, actual SDK conversation ID, native and projected event counts, review status, questions or review result, and `error=null`.

## Failure Behavior

- Missing or invalid LLM configuration: review returns HTTP 503 with `conversationMode="unavailable"`, `usedOpenHandsConversation=false`, and no fake review.
- SDK construction or run failure: review returns HTTP 503 with `conversationMode="failed"`, `usedOpenHandsConversation=false`, and a sanitized error.
- Conversation requests learner input: return HTTP 200 with `reviewStatus="awaiting_user"`, questions, no score, and no `review.completed` projection.
- Max iterations or native conversation error: treat as runtime failure unless a valid waiting or completed tool observation was already emitted.
- Repeated callback/reconciliation: skip native event IDs already projected.

## Testing Strategy

Tests use SDK `TestLLM` responses injected into the factory. They instantiate and run a real `LocalConversation`, a real SDK `Agent`, and native FocusProof tools while making no network request.

Required tests prove:

- factory returns `LocalConversation` and the initialized agent contains only allowed tools;
- lifecycle reuses one conversation and stable persistence paths;
- native `MessageEvent`, `ActionEvent`, and `ObservationEvent` exist in `state.events`;
- action and observation tool call IDs match, and action precedes observation;
- evidence verification ignores any LLM-provided body and loads by evidence ID;
- projection fields trace to native IDs and duplicate reconciliation is idempotent;
- runtime mode is derived from the injected/real runtime, not a request string;
- missing credentials and run failures return 503 without fallback;
- learner input prevents scoring and completion;
- final scoring consumes evidence, answers, and observation facts, not an LLM score;
- `real_llm` test is skipped by default and uses `.env` only when explicitly selected.

Finally run all requested pytest selections, Ruff, Mypy, the explicit real-LLM test, and an API session/evidence/review/events flow. Outputs and sanitized native/projected event samples are recorded in `docs/research/OPENHANDS_CONVERSATION_OFFICIAL_RUNTIME.md`.

## Migration

The official API stops importing or calling `DeterministicLearningAgentFallback`. `FocusProofLearningConversation` becomes a compatibility wrapper or is reduced to projection-only legacy support, with `openhands-fake` renamed to `projection-fallback` where it remains in isolated tests. The debug conversation endpoint reuses `ConversationFactory`. Existing tests that assert fake mode or fabricated OpenHands usage are replaced, not preserved.

## Known Initial Boundary

The manager is process-local, while the SDK event history is file-backed. A process restart can recreate a handle from the deterministic conversation UUID and persistence directory when the product session repository is available. Multi-process locking and remote Conversation/WebSocket support remain outside this task.
