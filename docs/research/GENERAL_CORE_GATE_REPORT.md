# General Core Gate Report

Status: not passed, externally blocked

## Scope

This report records the general knowledge learning-verification closure for FocusProof when Monad is disabled by default and only the official OpenHands Conversation path is used.

## Accepted Deterministic Evidence

- Monad plugin source remains in the repository but the default runtime disables it.
- Wallet, Monad, contract, and transaction-hash entry points render only when the enabled capability is present.
- Isolated Alembic DB: PASS.
- Backend deterministic regression: 30 passed.
- Frontend deterministic regression: 5 passed.
- Ruff, Mypy, lint, and diff-check: PASS.
- Independent review: APPROVED.

## Real-Provider Evidence

The official `/sessions/{id}/review` product path was exercised through OpenHands Conversation.

- `qwen-plus` was rejected as an invalid model format for the formal LiteLLM/OpenHands path.
- `openai/qwen-plus` reached DashScope successfully but failed with free quota exhausted.
- The OpenAI key was empty.

Because of those blockers, the dual-subject text and URL product acceptance is NOT PASSED / externally blocked.

## Commit Chain

- `2950e14`: restored structured runtime-unavailable responses.
- `8662a9d`: hid wallet UI when Monad is disabled.
- `f57c8b5`: corrected the DashScope/OpenAI-compatible model format to `openai/qwen-plus`.
- `bbd7fc9`: preserved stable API errors while logging a redacted root cause for server-side diagnostics.

## Next Gate

Restore usable real-provider quota or credentials, rerun the two-subject official `/sessions/{id}/review` product path, and only then move to AI5 multimodal work.

## Reuse Boundary

OpenHands is reused directly. This project does not introduce a mirror loop, a second EventLog, or a new protocol.
