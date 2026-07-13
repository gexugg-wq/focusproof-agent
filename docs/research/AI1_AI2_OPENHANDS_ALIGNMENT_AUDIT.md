# AI1 / AI2 OpenHands Alignment Audit

Version: v0.1
Source note reviewed: `D:/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk-study/OpenHands SDK 学习笔记.md`

## 1. Core Understanding From The SDK Notes

The OpenHands SDK should be understood as an event-driven agent runtime:

```text
Conversation factory
-> LocalConversation / RemoteConversation
-> ConversationState / EventLog / View
-> LocalConversation.run()
-> Agent.step()
-> LLM response dispatch
-> ActionEvent
-> ToolDefinition / ToolExecutor
-> ObservationEvent
-> EventLog / View for the next step
```

Key meanings:

- `Conversation` is a factory, not the state center. It chooses local or remote execution.
- `LocalConversation` is the local run container.
- `ConversationState` is the state owner.
- `EventLog` is the runtime fact ledger.
- `state.view` is the active-branch context given to the Agent/LLM.
- `run()` controls the loop; `Agent.step()` makes one decision.
- LLM tool calls become `ActionEvent` before execution.
- Tool results become `ObservationEvent` and return to the ledger.
- Agent Server is not a simple HTTP wrapper. It is the remote event runtime boundary.

For FocusProof, this means a learning session should become a Conversation-backed run, not only a FastAPI record plus a debug LLM call.

## 2. AI1 Audit

AI1 is aligned on language and project direction.

Passed:

- Created a Linux monorepo under `/home/holy/web3/focusproof-agent`.
- Established Python Agent Server as the runtime direction.
- Kept `frontend/` and `contracts/` as separate future areas.
- Investigated the local OpenHands SDK source.
- Produced `docs/research/OPENHANDS_SDK_FEASIBILITY.md`.
- Verified Python health tests, Ruff and Mypy.

Needs correction in interpretation:

- The AI1 feasibility report recommended local mirror interfaces from a lightweight MVP perspective.
- AI0 later overrode that decision: FocusProof should directly use OpenHands SDK and adapt around it.
- Future workers should treat AI1's report as evidence, not as the final integration decision.

Current verdict:

```text
AI1 passed.
No code rework required.
Its report needs to remain historically useful, but the task board now overrides the local-mirror recommendation.
```

## 3. AI2 Audit

AI2 is aligned on language and initial SDK dependency, but not yet fully aligned with OpenHands runtime semantics.

Passed:

- Kept AI2 implementation in Python.
- Added OpenHands SDK local path dependency.
- Created `focusproof.openhands_adapter`.
- Centralized SDK imports and capability reporting.
- Added safe tool policy.
- Disabled TerminalTool, FileEditorTool, browser automation, workspace mutation and ApplyPatch by default.
- Added `.env` loading and LLM config status without exposing secrets.
- Proved a real OpenHands LLM Conversation can run in a debug spike.
- Preserved FocusProof-owned learning models and scoring.
- Kept frontend out of the agent runtime.

Not aligned yet:

- Official `/sessions/{session_id}/review` does not run through an OpenHands `Conversation`.
- `OpenHandsConversationAdapter.create()` only records import capability and mode; it does not create or own a real `LocalConversation`.
- `InMemoryEventLog` is a FocusProof-only list-like ledger, not a projection from OpenHands `ConversationState._events`.
- `OpenHandsAgentAdapter.step()` is deterministic local logic, not an OpenHands `Agent.step()` path.
- `Action` and `Observation` are FocusProof Pydantic models, but not yet mapped to OpenHands `ActionEvent` and `ObservationEvent` in the official review path.
- Real LLM Conversation is currently isolated behind `/debug/openhands/conversation-test`.
- The reports previously said AI3 could proceed, but that is premature if the product goal is agent-runtime development rather than a UI prototype.

Current verdict:

```text
AI2 passed as SDK import + adapter + debug spike.
AI2 has not yet passed as the official OpenHands Conversation-backed FocusProof runtime.
AI3 should remain blocked unless AI0 explicitly chooses a temporary frontend prototype.
```

## 4. Required AI2 Correction

Next task:

```text
AI2-Next: Promote OpenHands Conversation To Core Review Runtime
```

Required behavior:

- Each FocusProof learning session creates or restores a Conversation-backed runtime.
- User goal/evidence/answer submissions enter the runtime as message/event input.
- Official `/sessions/{session_id}/review` runs through the Conversation-backed path.
- OpenHands events are projected into FocusProof audit events.
- Tool requests become ActionEvents before execution.
- Tool results become ObservationEvents before the next agent step.
- FocusProof scoring reads from the event/view projection, not from ad hoc API variables alone.
- Unsafe tools remain disabled by default.
- Debug endpoints remain available but are not the main review path.

Expected modules to add or change:

- `agent-server/focusproof/openhands_adapter/conversation.py`
- `agent-server/focusproof/openhands_adapter/events.py`
- `agent-server/focusproof/openhands_adapter/agent.py`
- `agent-server/focusproof/api/app.py`
- `agent-server/focusproof/runtime/event_log.py`
- `agent-server/tests/openhands_adapter/`
- `agent-server/tests/api/`
- `docs/research/OPENHANDS_CONVERSATION_CORE_INTEGRATION.md`

## 5. Acceptance Criteria Before AI3

AI3 should start only after these are true:

- `/sessions/{session_id}/review` can run in a Conversation-backed mode.
- The response exposes whether review used `conversationMode`.
- A test proves goal/evidence input is represented in the Conversation/Event path.
- A test proves Action-like review decisions are logged before tool execution.
- A test proves Observation-like tool results are logged before scoring.
- A test proves no unsafe OpenHands tools are enabled.
- A report explains how OpenHands EventLog and FocusProof audit events relate.

If these are not done, AI3 may still build a UI prototype only with an explicit AI0 exception.

## 6. Controller Rule Going Forward

AI0 must check future AI outputs against this rule:

```text
Do not confuse "OpenHands SDK can import" with "OpenHands runtime is integrated".
The integration is real only when ConversationState/EventLog/View drive the official review loop.
```
