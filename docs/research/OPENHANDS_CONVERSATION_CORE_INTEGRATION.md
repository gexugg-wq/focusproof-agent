# OpenHands Conversation Core Integration

> **Historical / Superseded.** This report describes an earlier fake/local runtime
> experiment. It is not the current architecture and must not be used as implementation
> guidance. The production path directly reuses the official OpenHands SDK and the
> boundaries indexed in `docs/architecture/ARCHITECTURE.md`.

## 1. Goal

This change promotes FocusProof review from a direct deterministic API loop into a Conversation-backed runtime shape. The official `POST /sessions/{session_id}/review` path now runs through `FocusProofLearningConversation`, which projects user messages, action-like decisions, tool observations, and final review events into the FocusProof audit ledger.

It fixes the prior gap where a real OpenHands Conversation existed only behind `/debug/openhands/conversation-test` while official review still bypassed Conversation semantics.

## 2. What Changed

Core changes:

- Added `focusproof.openhands_adapter.learning_conversation.FocusProofLearningConversation`.
- Added `ConversationReviewResult` with `conversationMode`, `usedOpenHandsConversation`, action/observation counts, unsafe tool policy, events, and `ReviewResult`.
- Added `focusproof.openhands_adapter.event_projection` helpers for MessageEvent-like, ActionEvent-like, ObservationEvent-like, and ReviewProjection events.
- Updated official `POST /sessions/{session_id}/review` to call `FocusProofLearningConversation.run_review(...)`.
- Strengthened `InMemoryEventLog` with `append_event`, `append_many`, `get_by_type`, sequence assignment, defensive copies, and latest/count/list behavior.
- Split `OpenHandsLearningAgentAdapter` from `DeterministicLearningAgentFallback` so fake mode is explicitly marked as `openhands-fake`.
- Added API and adapter tests for event projection, conversation-backed review, event order, weak evidence scoring, transaction scoring, and EventLog behavior.

## 3. Conversation Runtime Design

FocusProof now maps the OpenHands runtime model like this:

- `Conversation`: represented by `FocusProofLearningConversation.create(...)` as the official learning-review runtime entrypoint.
- `LocalConversation`: real OpenHands `Conversation(...)` remains available for debug/optional real LLM mode; default official review uses `openhands-fake` to avoid test/API key dependency.
- `ConversationState`: projected into a session-scoped runtime object that owns submitted goal/evidence/answers, action projections, observations, and review output.
- `EventLog`: FocusProof uses a projection EventLog for product/audit events. It is not yet the internal OpenHands EventLog.
- `View`: built from FocusProof goal, evidence, observations, and previous actions before each agent step.
- `Agent.step`: represented by `OpenHandsLearningAgentAdapter` and `DeterministicLearningAgentFallback`; fake mode emits FocusProof action projections.
- `ActionEvent`: projected through `project_action_to_focusproof_event(...)`.
- `ObservationEvent`: projected through `project_observation_to_focusproof_event(...)` after safe FocusProof tool execution.

The default event flow is:

```text
session.created
-> goal.submitted (MessageEvent-like)
-> evidence.submitted (MessageEvent-like)
-> verification.requested (ActionEvent-like)
-> verification.completed (ObservationEvent-like)
-> score.calculated (ReviewProjection)
-> review.completed (ReviewProjection)
```

## 4. Official Review Path

`POST /sessions/{session_id}/review` now runs through:

```text
FastAPI route
-> FocusProofLearningConversation.create(...)
-> submit evidence/answer as MessageEvent-like projections
-> run_review(...)
-> ActionEvent-like projection
-> safe FocusProof tool execution
-> ObservationEvent-like projection
-> FocusProof-owned scoring
-> review projection events
-> API response with conversation metadata
```

The response now includes:

- `conversationMode`
- `usedOpenHandsConversation`
- `actionEventsCount`
- `observationEventsCount`
- `unsafeToolsBlocked`
- `reviewResult`

Default mode is `openhands-fake`. This is intentional so automated tests and normal review calls do not consume real LLM credentials.

## 5. Event Projection

Projection helpers provide the boundary between OpenHands semantics and FocusProof audit events:

- `project_user_goal_to_message_event(...)`
- `project_evidence_to_message_event(...)`
- `project_answer_to_message_event(...)`
- `project_action_to_focusproof_event(...)`
- `project_observation_to_focusproof_event(...)`
- `project_openhands_output_to_review_input(...)`

Every projected payload includes:

- `runtimeSource`
- `sourceRuntime`
- `openhandsEventKind`
- `sourceIndex`
- `sessionId`
- `relatedEvidenceIds` where applicable

This keeps the FocusProof audit trail explicit about whether an event came from `openhands-real`, `openhands-fake`, or `fallback`.

## 6. Tool Safety

Dangerous OpenHands tools remain disabled:

- `TerminalTool`
- `FileEditorTool`
- `BrowserAutomation` / `BrowserTool`
- `WorkspaceMutationTool`
- `ApplyPatchTool`

The official review path uses safe FocusProof tools only:

- `FakeTextEvidenceTool`
- `FakeWeb3TxTool`

Tool facts still do not directly become learning scores. FocusProof scoring remains independent.

## 7. Real LLM Mode

The runtime supports a `useRealLlm` request flag on `/sessions/{session_id}/review`.

Current behavior:

- default: `useRealLlm=false`, `conversationMode="openhands-fake"`
- if `useRealLlm=true` and LLM config is available: `conversationMode="openhands-real"`
- if real config is unavailable: runtime falls back to `openhands-fake` with an explicit error message

The real debug path from AI2.6 remains available. The official scoring path does not rely on raw LLM output yet.

## 8. Tests

Required verification commands were run:

```text
pytest agent-server/tests -v
41 passed, 1 warning

ruff check agent-server
All checks passed!

mypy agent-server
Success: no issues found in 49 source files
```

The warning is the existing FastAPI/TestClient deprecation warning from dependencies.

## 9. Remaining Gaps

Known limitations:

- The official runtime uses a FocusProof projection EventLog, not the internal OpenHands `ConversationState._events` as the storage source of truth.
- OpenHands internal EventLog replacement remains a future integration step.
- WebSocket event streaming is not implemented.
- `RemoteConversation` is not used.
- Real Web3 RPC verification is not implemented.
- `openhands-real` mode is available as a mode boundary, but production scoring still uses deterministic FocusProof scoring over projected events.
- Recovery from persisted OpenHands ConversationState is not implemented; current persistence is in-memory/projection-scoped.

These gaps are documented rather than hidden because FocusProof still needs a clean product audit ledger while OpenHands runtime integration matures.

## 10. Decision For AI3

AI3 can proceed.

AI3 should call:

- `POST /sessions`
- `POST /sessions/{session_id}/evidence`
- `POST /sessions/{session_id}/answer`
- `POST /sessions/{session_id}/review`
- `GET /sessions/{session_id}`
- `GET /sessions/{session_id}/events`
- `GET /health`
- `GET /openhands/capabilities`

AI3 can rely on `conversationMode`, `usedOpenHandsConversation`, `actionEventsCount`, and `observationEventsCount` in the review response for runtime status display or diagnostics.

AI3 must not call debug LLM APIs as part of the production user flow. Debug APIs remain backend diagnostics only.
