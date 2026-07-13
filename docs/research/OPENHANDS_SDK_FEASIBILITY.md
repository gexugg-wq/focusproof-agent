# OpenHands SDK Feasibility Report

## 0. AI0 Product Decision Override

AI1 originally recommended **Local Mirror Interfaces** from a lightweight MVP perspective. AI0 has now changed the product direction: FocusProof is not intended to be a disposable demo, so AI2 should directly use OpenHands SDK where feasible and build a FocusProof adapter layer around it.

Updated decision:

- Primary strategy: **Direct OpenHands SDK integration with FocusProof adapters**.
- Do not build a parallel local mirror runtime as the main path.
- Keep FocusProof-owned learning models for Evidence, ReviewResult, scoring dimensions, Build Log and on-chain proof payload.
- Disable or avoid risky software-engineering tools by default, especially TerminalTool, FileEditorTool and workspace mutation tools.
- If a concrete OpenHands SDK class cannot be instantiated safely, document the blocker and create a narrow fallback shim behind the adapter.

AI2 should treat the rest of this report as feasibility evidence, not as the final architecture decision.

## 1. Local SDK Paths Checked

| Path | Status | Notes |
|---|---|---|
| `/home/holy/.openhands` | Exists | Local OpenHands user/runtime directory. Useful for environment context, not a source dependency. |
| `/home/holy/.openbrowser` | Exists | Local OpenBrowser user/runtime directory. Useful for checking adjacent browser-agent experiments. |
| `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main` | Exists | Primary local OpenHands software-agent SDK source tree. |
| `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk-study` | Exists | Local study notes, including OpenBrowser-to-OpenHands mapping. |
| `/home/holy/.cache/uv/archive-v0/he9BS_ntKEOTh2Kk/openhands` | Exists | Installed `openhands_sdk-1.12.0` cache copy found by fallback search. |
| `/home/holy/.cache/uv/archive-v0/ds8Fd-tEEFUPCSa8/openhands` | Exists | Installed `openhands_tools-1.12.0` cache copy found by fallback search. |
| `/home/holy/code/OpenBrowser` | Exists | Local OpenBrowser project found by fallback search. |
| `/mnt/d/研一/code_study/OpenHands/OpenHands-main` | Exists | Full OpenHands product source, not recommended as FocusProof base. |

## 2. Potentially Reusable Parts

### AgentBase / Agent step pattern

- Name: `AgentBase`, `Agent`
- Path: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/agent/base.py`, `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/agent/agent.py`
- Role: Defines agent configuration, LLM/tool setup and step-oriented execution around tool calls.
- Recommend direct import: No for MVP.
- Reason: The implementation imports LLM, MCP, prompt registries, default tools, critics, security analysis, observability and model-specific helpers. It is useful as a reference for one-action-at-a-time agent boundaries, but too broad for the FocusProof scaffold.

### Conversation interfaces and state

- Name: `BaseConversation`, `Conversation`, `ConversationStateProtocol`
- Path: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/conversation/base.py`, `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/conversation/conversation.py`
- Role: Provides a conversation container, run control, state access, local/remote factory and callbacks.
- Recommend direct import: No for MVP.
- Reason: Strong conceptual match, but the factory is tied to workspaces, plugins, callbacks, remote/local runtimes, observability and OpenHands-specific execution status. FocusProof should first mirror a thinner learning-review conversation.

### Event and LLM-convertible event model

- Name: `Event`, `ActionEvent`, `ObservationEvent`
- Path: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/event/base.py`, `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/event/llm_convertible/action.py`, `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/event/llm_convertible/observation.py`
- Role: Typed immutable events with source, timestamp, parent linkage and LLM message conversion.
- Recommend direct import: No for MVP.
- Reason: The event separation is valuable, especially ActionEvent versus ObservationEvent. FocusProof already has a public learning protocol in `docs/protocol/EVENTS.md`, so importing OpenHands event classes would mix software-agent semantics into learning evidence semantics.

### EventLog persistence pattern

- Name: `EventLog`, `EventsListBase`
- Path: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/conversation/event_store.py`, `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/conversation/events_list_base.py`
- Role: Append-only event list with file-store persistence, locking, event ID index and branch/path traversal.
- Recommend direct import: No for MVP; adapt ideas.
- Reason: The locking and append-only model are relevant. Direct import would also require OpenHands `FileStore`, event classes and workspace assumptions. AI2 should implement a FocusProof EventLog against FocusProof event models.

### Tool schema and executor shape

- Name: `ToolExecutor`, `ToolDefinition`, `Action`, `Observation`, `Schema`
- Path: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/tool/tool.py`, `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/tool/schema.py`
- Role: Typed tool action/observation schemas, JSON schema export, executor boundary and resource hints.
- Recommend direct import: No for MVP; mirror locally.
- Reason: The Action/Observation split is a very good model, but FocusProof tools should return learning-verification observations, not OpenHands tool output objects. Local mirrors keep the dependency light and domain-safe.

### Agent server route patterns

- Name: `conversation_router`, `event_router`
- Path: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-agent-server/openhands/agent_server/conversation_router.py`, `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-agent-server/openhands/agent_server/event_router.py`
- Role: FastAPI route organization for conversations and events.
- Recommend direct import: No.
- Reason: Useful as a route-boundary reference, but endpoints are OpenHands product endpoints. FocusProof should expose `/sessions`, `/sessions/{id}/events`, `/review` and Build Log APIs defined by AI2 requirements.

## 3. Reference-Only Parts

- TerminalTool: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-tools/openhands/tools/terminal/`
- FileEditorTool: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-tools/openhands/tools/file_editor/`
- Browser automation: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-tools/openhands/tools/browser_use/`
- Apply patch and workspace mutation tooling: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-tools/openhands/tools/apply_patch/`
- Task tracking and delegation tools: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-tools/openhands/tools/task/`, `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-tools/openhands/tools/delegate/`
- Software task loop, finish logic and critic flow: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/agent/agent.py`, `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/conversation/goal/`
- OpenBrowser mapping notes: `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk-study/openbrowser-mapping.md`

These parts show useful boundaries: agent decides, tool executes, observation returns, event log records. They should not be imported into the FocusProof MVP because they optimize for software and browser task execution rather than learning-evidence verification.

## 4. Do-Not-Use Parts For MVP

- Do not enable `TerminalTool` by default. It executes shell commands and belongs only in a future programming-learning plugin with explicit safeguards.
- Do not enable `FileEditorTool` by default. It mutates files and is not evidence verification for general learning sessions.
- Do not let OpenHands finish/task-completion logic replace FocusProof scoring. Completing a software task is not the same as proving learning.
- Do not let OpenHands software-engineering task loops shape the core FocusProof learning loop. FocusProof needs goal, evidence, question, answer, observation, scoring and review semantics from `docs/protocol/EVENTS.md`.
- Do not use full `RemoteConversation` or `openhands-agent-server` as the MVP backend. They pull in workspaces, secrets, agent profiles, tools, plugins, sockets and product-specific routes.
- Do not add `openhands-sdk` as a direct dependency yet. The local SDK `pyproject.toml` requires Python `>=3.12`, while FocusProof AI1 requirements say Python `>=3.11`.

## 5. Recommended Integration Strategy

Recommended option: **C. Local Mirror Interfaces**.

AI2 should implement thin local interfaces named and shaped after the OpenHands concepts, but owned by FocusProof:

- `EventLog`
- `Conversation`
- `ConversationState`
- `AgentView`
- `Agent.step(view) -> Action`
- `Action`
- `Observation`
- `ToolDefinition`
- `ToolExecutor`

Reasons:

- FocusProof already has public learning protocol semantics in `docs/protocol/EVENTS.md`.
- Direct OpenHands imports are too heavy for the first scaffold and require Python `>=3.12`.
- The useful OpenHands ideas are architectural boundaries, not product behavior.
- Local interfaces keep AI2 free to implement learning evidence, scoring and domain plugins without inheriting software-agent task assumptions.
- Later, if the project upgrades to Python 3.12 and proves that selected SDK abstractions are stable enough, adapters can wrap OpenHands types behind FocusProof-owned interfaces.

## 6. Suggested Next Step For AI2

AI2 should not directly import OpenHands SDK classes in the first runtime pass.

AI2 should first implement local mirror interfaces:

- `focusproof.runtime.events`: Pydantic event models matching `docs/protocol/EVENTS.md`.
- `focusproof.runtime.event_log`: append/list/get-by-type/latest/count API with monotonic sequence guarantees.
- `focusproof.runtime.actions`: FocusProof `Action` union.
- `focusproof.runtime.observations`: FocusProof `Observation`.
- `focusproof.runtime.tools`: `ToolDefinition` and `ToolExecutor` protocols.
- `focusproof.runtime.conversation`: `ConversationState` and safe run-loop shell.
- `focusproof.agents.base`: `Agent.step(view)` protocol.

AI2 should use these OpenHands files as references while implementing:

- `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/tool/tool.py`
- `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/tool/schema.py`
- `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/conversation/event_store.py`
- `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/conversation/base.py`
- `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/event/llm_convertible/action.py`
- `/mnt/d/研一/code_study/OpenHands/software-agent-sdk-main/openhands-sdk/openhands/sdk/event/llm_convertible/observation.py`

AI2 can consider an adapter layer only after the local FocusProof protocol is tested and stable.
