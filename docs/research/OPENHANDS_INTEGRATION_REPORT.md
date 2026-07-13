# OpenHands Integration Report

## 1. Dependency Method

FocusProof uses a local path dependency for OpenHands SDK:

- `openhands-sdk @ file:///mnt/d/%E7%A0%94%E4%B8%80/code_study/OpenHands/software-agent-sdk-main/openhands-sdk`

The dependency was also installed into the project venv with:

```bash
uv pip install -e "/mnt/d/??/code_study/OpenHands/software-agent-sdk-main/openhands-sdk"
```

Adapter mode is currently `direct` when the SDK imports succeed. The MVP does not instantiate a real OpenHands LLM conversation yet; it uses FocusProof's adapter and fake review tools for deterministic tests.

## 2. Imported SDK Modules

Verified imports:

- `openhands`: editable namespace package; `openhands.__file__` is `None`, with namespace path from the editable install hook.
- `openhands.sdk.agent`: `/mnt/d/??/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/agent/__init__.py`
- `openhands.sdk.conversation`: `/mnt/d/??/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/conversation/__init__.py`
- `openhands.sdk.tool`: `/mnt/d/??/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/tool/__init__.py`
- `openhands.sdk.event`: `/mnt/d/??/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/event/__init__.py`

## 3. Adapter Layer

Created `agent-server/focusproof/openhands_adapter/`:

- `__init__.py`: exports adapter capability and import helpers.
- `sdk_imports.py`: centralizes all OpenHands SDK imports and reports direct/partial/fallback status.
- `capabilities.py`: returns stable capability summaries for API and tests.
- `events.py`: preserves FocusProof event payloads when converting to OpenHands-shaped messages.
- `tools.py`: enforces disabled OpenHands tool policy and routes safe FocusProof fake tools.
- `conversation.py`: records OpenHands conversation adapter mode and blocker reason.
- `agent.py`: adapts FocusProof `AgentView` into one action per step.
- `errors.py`: defines `OpenHandsIntegrationError`, `UnsafeOpenHandsToolError`, and `OpenHandsCapabilityMissingError`.

## 4. Disabled OpenHands Tools

Disabled by default:

- `TerminalTool`: shell execution is not safe general learning evidence.
- `FileEditorTool`: workspace mutation is not proof of learning.
- `BrowserAutomation` / `BrowserTool`: browser control is not enabled for this MVP.
- `WorkspaceMutationTool` / `ApplyPatchTool`: mutation tools are reserved for future explicit programming-learning plugins.

Tool observations remain facts only. They do not directly produce learning scores.

## 5. FocusProof-Owned Models

FocusProof still owns:

- `LearningGoal`
- `Evidence`
- `Action`
- `Observation`
- `AgentView`
- `Finding`
- `ReviewResult`
- scoring dimensions and review statuses
- future Build Log and proof payload semantics

OpenHands provides imported runtime concepts and adapter inspiration, but FocusProof scoring remains independent.

## 6. Current Limitations

Current state:

- Real LLM is not wired.
- Real OpenHands `Conversation` is not started.
- Real OpenHands tools are not executed.
- The MVP completes direct SDK import + FocusProof adapter + deterministic fake tool mode.
- The OpenHands top-level package is an editable namespace package, so `openhands.__file__` is `None`; concrete SDK module paths are available.

Original lightweight-MVP reading: this did not block a frontend prototype because the required session/review APIs were available and testable.

AI0 correction after reviewing the OpenHands SDK study notes:

```text
This does block the main product path if the goal is agent-runtime development.
The official review path still needs OpenHands Conversation / ConversationState / EventLog integration before AI3 starts as the main next phase.
```

## 7. Previous Frontend API Notes

A temporary frontend prototype could call:

- `GET /health`
- `GET /openhands/capabilities`
- `POST /sessions`
- `POST /sessions/{session_id}/evidence`
- `POST /sessions/{session_id}/answer`
- `POST /sessions/{session_id}/review`
- `GET /sessions/{session_id}`
- `GET /sessions/{session_id}/events`

The frontend should treat review results as server-owned. It should not call an LLM, write the database directly, or calculate scores client-side.

Current AI0 decision:

```text
Do not start AI3 as the main next phase yet.
Run AI2-Next first: promote OpenHands Conversation into the official `/sessions/{session_id}/review` orchestration path.
```
