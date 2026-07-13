# AI4A Codex Goal: General Verification Framework

## Objective

Implement the approved AI4A General Verification Framework end to end. Extend
the existing OpenHands Conversation-backed review runtime with a FocusProof
capability registry, deterministic per-session tool assembly, and safe text and
URL evidence verification.

This design is already approved. Do not reopen broad product design or replace
the runtime. Execute the written implementation plan task by task using TDD.

## Workspace And Baseline

Project:

```text
/home/holy/web3/focusproof-agent
```

Minimum control baseline:

```text
branch: main
minimum commit: 20d33f8
```

The actual starting HEAD may be a later AI0 documentation commit and must
contain this Goal file. Record that full SHA before creating the work branch.

Create and work only on:

```text
ai4a-general-verification-framework
```

Do not push. Do not rewrite existing history.

## Required Reading

Read these files completely before modifying code:

1. `docs/architecture/ARCHITECTURE.md`
2. `docs/architecture/OPENHANDS_REUSE_STRATEGY.md`
3. `docs/protocol/EVENTS.md`
4. `docs/project-management/TASK_BOARD.md`
5. `docs/superpowers/specs/2026-07-13-ai4a-general-verification-framework-design.md`
6. `docs/superpowers/plans/2026-07-13-ai4a-general-verification-framework.md`
7. `docs/research/OPENHANDS_CONVERSATION_OFFICIAL_RUNTIME.md`
8. `docs/research/PERSISTENCE_RUNTIME_HARDENING_REPORT.md`
9. `agent-server/focusproof/openhands_runtime/`
10. `agent-server/tests/openhands_runtime/`
11. `agent-server/focusproof/domain/scoring.py`
12. `agent-server/tests/domain/test_scoring.py`

The design specification is the requirements authority. The implementation plan
defines task order, interfaces, tests, commands, and commit boundaries.

## Required Workflow

Use `superpowers:executing-plans` or
`superpowers:subagent-driven-development`. Use
`superpowers:test-driven-development` for every production behavior and
`superpowers:verification-before-completion` before reporting success.

Before editing, report:

- resolved project root;
- current branch and full HEAD SHA;
- `git status --short --branch`;
- OpenHands SDK dependency source and installed version;
- current `Agent` tool list and `include_default_tools` value;
- current ActionEvent/ObservationEvent execution path;
- files expected to change;
- protected directories that will remain untouched.

If the worktree is not clean, do not discard or overwrite changes. Determine
whether they belong to this task and stop for AI0 only if they prevent safe work.

## Non-Negotiable Runtime Boundaries

- Continue using OpenHands `Agent`, `LocalConversation`,
  `ConversationState`, native EventLog, `ToolDefinition`, `ToolExecutor`,
  Action, Observation, ActionEvent, and ObservationEvent directly.
- Do not implement a parallel Conversation, EventLog, Agent loop, or executable
  tool protocol.
- Agent actions carry `evidence_id`; trusted server construction injects
  `session_id`.
- The LLM cannot supply authoritative evidence bodies, repository objects,
  network clients, credentials, or filesystem paths as tool arguments.
- Executors load evidence through the FocusProof repository.
- Tools return facts and limitations only. They cannot assign final numeric
  scores, final learning status, or judgments about learner character.
- Native OpenHands events remain runtime facts. FocusProof audit events remain
  idempotent projections.
- Keep `include_default_tools=[]`.
- Do not enable terminal, file editor, browser automation, patch, or workspace
  mutation tools.
- Default tests must not consume a real LLM key.

## Required Deliverables

Execute all eight tasks in the implementation plan:

1. Capability metadata and thread-safe FocusProof policy registry.
2. Shared evidence-reference Action and fact-only Verification Observation.
3. Repository-backed text evidence verification ToolDefinition.
4. SSRF-safe bounded URL policy, fetcher, and URL ToolDefinition.
5. OpenHands SDK registration and deterministic per-session tool assembly.
6. Capability-neutral prompt, native event extraction, and projection updates.
7. Removal of Web3 assumptions from general scoring.
8. Full regression, recovery, security verification, and final report.

The final report must be:

```text
docs/research/AI4A_GENERAL_VERIFICATION_FRAMEWORK_REPORT.md
```

## Allowed Changes

- `agent-server/focusproof/openhands_runtime/`
- narrowly affected modules under `agent-server/focusproof/domain/`
- `agent-server/tests/`
- `fixtures/`
- `docs/research/`
- necessary Python dependency declarations

## Forbidden Changes

- `frontend/`
- `contracts/`
- `.env`
- `var/`
- OpenHands SDK source
- GitHub history or remote branches
- public architecture, protocol, task-board, design, or plan documents without
  explicit AI0 approval
- code execution, Web3 RPC, OCR, ASR, PDF processing, contract work, deployment,
  or AI4B work

## Execution Rules

- Follow Tasks 1 through 8 in order.
- For each task, run the focused test first and capture the expected red state.
- Implement the smallest behavior satisfying that task.
- Run the focused tests, Ruff, and Mypy specified in the plan.
- Inspect staged paths before every commit.
- Use the commit messages defined by the plan.
- Do not combine all work into one commit.
- Do not weaken or delete an existing test merely to make the suite pass.
- Do not use the public network in default URL verifier tests; use injected DNS
  and HTTP fakes.
- Do not print or modify API keys.
- Do not call a real LLM unless AI0 explicitly authorizes the marked smoke test.

If the installed OpenHands SDK API differs from an interface assumed by the
plan, inspect the installed SDK source and adapt at the narrow FocusProof
boundary. Preserve the design invariants, add a regression test, and document
the exact difference in the report. Do not patch or fork the SDK.

## Required Final Verification

Run at minimum:

```bash
.venv/bin/python -m pytest agent-server/tests -q -m "not real_llm"
.venv/bin/ruff check agent-server
.venv/bin/mypy agent-server
git diff --check main...HEAD
git diff --name-status main...HEAD
git status --short --branch
git log --oneline --decorate main..HEAD
```

Reject completion if protected paths appear in the diff, default OpenHands tools
are enabled, observations contain scores/verdicts, URL safety tests are missing,
or the official review endpoint no longer uses LocalConversation.

## Stop Condition

After all plan tasks, local commits, report, and verification are complete:

- stop without pushing;
- do not merge into `main`;
- do not begin AI4B;
- return changed files, interfaces, commit list, commands, exact test results,
  known limitations, protocol-change status, protected-path status, and report
  path to AI0 for acceptance.
