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
# General Core Gate Replay Harness

`scripts/run_general_core_gate.py` is a Linux/Python 3.12 acceptance harness, not a runtime.
It reuses the production FastAPI routes and therefore the existing FocusProof-owned persistence,
scoring and API projection around the OpenHands SDK `Conversation`. It defines no second agent
loop, conversation, event protocol, action/observation model, or tool protocol.

The harness runs two bounded scenarios (photosynthesis text and a Python closure public HTTPS
URL), answers dynamic learner questions, and records final review fields, Conversation ID,
native projected events, and the API event stream used as the Build Log. Provider/account/network
unavailability is `BLOCKED`; acceptance assertion failures are `FAIL`; no fallback is allowed.

The server data directory and migrated SQLite database live under an automatically removed
`/tmp/focusproof-general-core-gate-*` directory. Monad remains source-present but is forced off,
and its public capability count must remain zero.

The acceptance deadline is shared across capability discovery and both scenarios; every HTTP
request receives only the remaining monotonic-clock budget. Network/provider timeouts are
`BLOCKED`, while exhaustion of the bounded learner interaction budget is `FAIL`. Build Log is
the independent official `/sessions/{id}/events` audit stream. Native OpenHands evidence is
reported separately from the official review response and requires both Action and Observation
counts to be positive.

The server inherits a pre-bound IPv4 loopback socket, eliminating the choose-port/release race.
It runs from the repository root with LiteLLM's official `LITELLM_MODE=PRODUCTION` switch, so a
caller-controlled working directory `.env` is not loaded. Startup, migration, and socket faults
are harness `FAIL` results rather than provider `BLOCKED` results.
