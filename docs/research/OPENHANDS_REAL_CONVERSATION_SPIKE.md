# OpenHands Real Conversation Spike

## 1. Goal

This spike verifies whether a real OpenHands SDK LLM Agent/Conversation path can be safely used as a FocusProof backend debug-only path for learning evidence review. It does not connect the real LLM path to the production `/sessions/{id}/review` scoring flow.

Safety boundaries remain mandatory:

- no shell execution,
- no file editing,
- no browser automation,
- no workspace mutation,
- no API key exposure through logs, reports, tests, or debug API responses.

## 2. Environment Status

Current safe status check:

- env file exists: true
- dotenv format valid: true
- has key in valid dotenv config: true
- base URL configured: true
- model: `openai/qwen3.6-flash-2026-04-16`
- LLM config can be built: true

The earlier PowerShell `$env:` syntax blocker has been resolved. The `.env` contents and credentials were not printed or copied into this report.

When using DashScope through OpenAI-compatible routing, the model name must use LiteLLM/OpenAI-compatible provider form such as `openai/<model>`.

## 3. SDK Usage

The debug path uses the SDK's real public imports:

- `from openhands.sdk import LLM`
- `from openhands.sdk import Agent`
- `from openhands.sdk import Conversation`

The implemented debug spike builds:

```python
llm = LLM(usage_id="focusproof-debug", model=model, api_key=SecretStr(...), base_url=base_url)
agent = Agent(llm=llm, tools=[])
conversation = Conversation(
    agent=agent,
    workspace=temp_workspace,
    persistence_dir=temp_persistence_dir,
    max_iteration_per_run=3,
    visualizer=None,
)
```

The path explicitly passes `tools=[]`, uses temporary directories, and is still debug-only.

## 4. Tool Safety

Disabled tools:

- `TerminalTool`: can execute shell commands.
- `FileEditorTool`: can mutate files.
- `BrowserAutomation` / `BrowserTool`: can browse or automate web pages.
- `WorkspaceMutationTool`: can mutate project state.
- `ApplyPatchTool`: can edit workspace files.

Manual and automated checks did not enable these tools. The real OpenHands agent receives no tool specs.

## 5. API Added

Debug APIs remain:

- `GET /debug/openhands/env-status`
- `GET /debug/openhands/llm-status`
- `POST /debug/openhands/conversation-test`

These APIs return capability/status and debug result fields only. They do not return API keys.

The production session review path is unchanged and remains deterministic/fake-tool-backed for now.

## 6. Automated Test Results

Required command results after cleanup:

```text
pytest agent-server/tests/openhands_adapter agent-server/tests/api/test_openhands_debug_api.py -q
20 passed, 1 warning

ruff check agent-server
All checks passed!

mypy agent-server
Success: no issues found in 43 source files
```

The remaining warning is the FastAPI/TestClient upstream deprecation warning from the installed dependency set.

## 7. Manual LLM Run Result

Manual real spike was run with a Web3 transaction-hash learning prompt.

Safe summary:

- attempted real LLM: yes
- mode: `real`
- model: `openai/qwen3.6-flash-2026-04-16`
- recommendedAction: `request_evidence`
- rawText present: true
- reason parsed: true
- question parsed: model-dependent; parser supports JSON and Markdown `Question:` fields, but empty/missing model output is returned as `None`
- dangerous tools executed: no
- API key printed: no

Raw LLM output is intentionally not pasted here. The parser now accepts JSON and Markdown-style fields such as `**Action:**`, `**Question:**`, and `**Reason:**`.

## 8. Output Noise

Safe local noise reduction was added:

- `OPENHANDS_SUPPRESS_BANNER=1` is set for the debug run if not already set.
- `Conversation(..., visualizer=None)` suppresses the default system-prompt visualizer output.
- temporary `persistence_dir` avoids the in-memory persistence warning.
- Python logging is temporarily disabled up to WARNING during the debug run, then restored; errors are not suppressed.
- LiteLLM cost-map warnings are locally filtered during the run.

This is intentionally local to the debug function and does not hide returned failure results.

## 9. Revised Decision For AI3

Original spike conclusion said AI3 could proceed because a debug-only real LLM path existed and normal session APIs were testable.

AI0 correction after reviewing the OpenHands SDK study notes:

```text
AI3 should not be the main next phase yet.
The real OpenHands Conversation must first be promoted from debug-only path into the official learning review runtime.
```

If AI0 explicitly allows a temporary UI prototype, frontend usage remains:

- Use normal FocusProof session APIs for product flow.
- Treat the real OpenHands LLM path as backend debug-only.
- Do not call LLMs from the frontend.
- Do not calculate final scores on the frontend.
- Do not depend on the debug endpoint for production review UX.

Backend decision:

- Replace the current deterministic/fake-tool-backed orchestration with a Conversation-backed runtime before treating the backend as architecturally aligned.
- Do not promote raw LLM output into official FocusProof scoring until schema enforcement, rate limits, and stronger validation are added.

Required next backend phase:

```text
AI2-Next: Promote OpenHands Conversation To Core Review Runtime
```

## 10. Remaining Risks

- The current API key should be rotated because it has been used during manual debugging.
- OpenHands/LiteLLM output can still be noisy outside this local debug wrapper.
- Raw LLM output is not strong enough for production review without stricter schema enforcement.
- LiteLLM cost warnings for unmapped `openai/<model>` DashScope models do not block functionality, but they can affect cost accounting.
- Real LLM latency and provider failures must be treated as debug-path behavior, not production review reliability.
