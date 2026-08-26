# General Core Gate Report

Status: PASS (2026-08-12)

> Current-status note (2026-08-14): This is a historical General Core report.
> Its Next Stage statement is superseded. Current roadmap and acceptance
> status are maintained in
> [TASK_BOARD](../project-management/TASK_BOARD.md) and
> [AI5_IMAGE_GATE_REPORT](AI5_IMAGE_GATE_REPORT.md): image input and real visual
> interpretation passed, AI5.3 implementation/code gates passed, and the real
> external clamd clean/EICAR gate remains blocked. No later formal phase name is
> approved.

## Scope

This report records the general knowledge learning-verification closure for FocusProof when Monad is disabled by default and only the official OpenHands Conversation path is used.

## Formal Real-Provider Gate

The formal General Core Gate passed on 2026-08-12 through the production
FastAPI review path with the following redacted configuration:

- Provider: `dashscope-openai-compatible`.
- Model: `openai/qwen3.7-plus`.
- Monad capability count: `0`.
- Structured report: `/tmp/focusproof-general-core-gate-qwen37.json`.
- Secret scan: `true` (no configured secret appeared in the report or captured output).

Both scenarios retained non-empty OpenHands Conversation IDs:

| Scenario | Result | Score | Confidence | Questions | Build Log | Native Actions | Native Observations |
|---|---:|---:|---:|---:|---:|---:|---:|
| `photosynthesis-text` | PASS | 65 | 0.72 | 0 | 7 | 2 | 2 |
| `python-closure-url` | PASS | 63 | 0.72 | 1 | 9 | 3 | 3 |

The first scenario completed directly, which is a supported product outcome. The
second scenario generated a subject-specific learner question before completion,
so the two-subject gate also demonstrated dynamic follow-up behavior.

## Runtime Evidence and Boundary

The successful path was the formal production chain:

`FastAPI -> OpenHands SDK Conversation -> Agent.step -> native Action/Observation/EventLog`

It did not use `TestLLM`, a fallback runtime, or a debug endpoint. OpenHands is
reused directly; FocusProof does not implement a second runtime loop, EventLog,
Action/Observation model, or tool protocol.

## Accepted Supporting Evidence

- Monad plugin source remains in the repository but the default runtime disables it.
- Wallet, Monad, contract, and transaction-hash entry points render only when the enabled capability is present.
- Isolated Alembic DB: PASS.
- Deterministic backend and frontend regressions: PASS.
- Ruff, Mypy, lint, and diff-check: PASS.
- Independent review: APPROVED.

## Next Stage

The next stage is AI5 multimodal input foundation. AI5 has not started.

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
