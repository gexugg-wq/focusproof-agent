# AI4A.3.1 Lifecycle Atomicity Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four AI0 acceptance gaps in URL executor lifecycle atomicity, released-provider behavior, Future timeout classification, and persisted evidence-context migration.

**Architecture:** Keep OpenHands SDK 1.31.0 as the only Agent, Conversation, EventLog, tool, Action/Observation, interrupt, and close runtime. FocusProof's existing bounded URL execution pool remains only an application-level boundary for blocking I/O; executor state locking linearizes close against submit/registration, while persisted context migration appends one native versioned MessageEvent without rewriting history or projecting another product fact.

**Tech Stack:** Python 3.12, OpenHands SDK 1.31.0, `concurrent.futures`, SQLAlchemy/SQLite, Pytest, Ruff, Mypy.

## Global Constraints

- Preserve all existing uncommitted AI4A.3 work; do not reset, checkout, restore, overwrite, or delete it.
- Do not modify frontend, contracts, database models, Alembic, API contracts, scoring, `.env`, `var`, or OpenHands SDK source.
- Do not modify or stage `README.md`, `docs/README.md`, `docs/architecture/ARCHITECTURE.md`, `docs/architecture/OPENHANDS_REUSE_STRATEGY.md`, `docs/project-management/TASK_BOARD.md`, or `docs/protocol/EVENTS.md`.
- Do not create a second Runtime, Agent loop, EventLog, Action/Observation model, tool registry protocol, or cancellation token.
- Do not read, print, or commit API keys. Do not push, merge, or begin AI4B.
- Write and run each regression test RED before changing its production path; create local commits only after every final gate passes.

## Acceptance Gaps And Reproduction

1. **Executor close/submit race:** pause the execution pool inside `submit()`, start a URL call, call `executor.close()` concurrently, then release submit. Current code submits outside `_state_lock`, so close can return before the Future is registered and fetch may start afterward.
2. **Released Provider late call:** construct an executor with repository/fetcher/pool resolved from global providers, close it, release providers, then call it. Current code resolves the repository before checking `_closed`, raising `RuntimeError` instead of returning `verifier_closed`.
3. **Future TimeoutError ambiguity:** a completed fetch Future raises built-in `TimeoutError`. In Python 3.12 it is caught by the `FutureTimeoutError` handler; current code does not check `future.done()` and loops until the total deadline.
4. **Persistence migration gap:** current evidence-context test synchronizes twice without closing the first `LocalConversation`; it does not prove SDK persistence restore, immutable old JSON, or second-restore idempotence.

## Native OpenHands Reuse Boundary

- Reuse the existing OpenHands `Agent`, `LocalConversation`, `conversation.state.events` EventLog, `ToolDefinition`, `ToolExecutor`, `ActionEvent`, `ObservationEvent`, `MessageEvent`, `interrupt()`, and `close()` paths.
- The bounded URL pool executes only FocusProof's blocking fetch callable; it does not dispatch tools or run an Agent loop.
- Evidence migration writes through `LocalConversation.send_message()` and reads native `MessageEvent.llm_message`; it never mutates Conversation state or replays callbacks.

## Lifecycle Rules

- **Submit linearizes first:** while holding the executor state lock, observe open, submit to the bounded pool, and register `(interrupt_event, Future)`. A concurrent close waits for this short non-network critical section, then marks closed and cancels/signals the registered call.
- **Close linearizes first:** once close marks `_closed` while holding the state lock, every later call returns `verifier_closed` without provider lookup or pool submission.
- `close()` is idempotent. It snapshots active calls under the lock and performs `Event.set()` / `Future.cancel()` outside it. It never waits for network tasks while holding the state lock.
- A queued Future cancelled by close never starts. A Future already running before close may continue if non-cooperative, but the global pool worker bound limits it; Python does not forcibly terminate threads.
- `UrlExecutionBusyError` maps only to `verifier_busy`; `UrlExecutionPoolClosedError` maps only to `verifier_closed`.

## Provider Rules

- Check executor `_closed` before resolving repository, fetcher, or pool providers, returning a safe Observation based only on `action.evidence_id`.
- Preserve `evidence_not_found`, unsupported evidence, and `source_url_missing` behavior for open executors.
- Repeat the atomic `_closed` check at the submit/registration linearization point to prevent a provider-resolution TOCTOU race.
- Provider release remains application ownership cleanup; a closed executor must never access a released provider.

## Future Timeout Classification

- Keep `future.result(timeout=min(remaining, 0.01))` for interrupt responsiveness.
- On `FutureTimeoutError`, check `future.done()`: false means only the wait timed out and polling may continue; true means the task itself raised built-in `TimeoutError`, which maps immediately to a safe `UrlFetchError("network_timeout", ...)`.
- Never expose the original exception text or authoritative URL data.

---

### Task 1: Plan And Baseline Evidence

**Files:**
- Create: `docs/superpowers/plans/2026-07-14-ai4a3-1-lifecycle-atomicity-repair.md`

- [ ] Record current HEAD, status, six protected file hashes, SDK version, and the current focused baseline (`5 passed`).
- [ ] Confirm allowed implementation files are `tools/url_evidence.py`, `tools/url_execution.py`, `tool_registry.py`, `factory.py`, `evidence_messages.py`, and `synchronizer.py`; allowed tests are the two AI4A.3 files plus directly affected existing runtime tests; allowed docs are this plan and the research report.

### Task 2: RED/GREEN Executor Close-Submit Atomicity

**Files:**
- Modify: `agent-server/tests/openhands_runtime/test_ai4a3_resource_bounds.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/url_evidence.py`
- Modify only if required: `agent-server/focusproof/openhands_runtime/tools/url_execution.py`

**Interfaces:**
- Consumes: `BoundedUrlExecutionPool.submit(operation) -> Future[T]`, `UrlEvidenceVerificationExecutor.close()`.
- Produces: one state-lock critical section covering open check, pool submit, and active Future registration.

- [ ] Add a controllable GatePool test that blocks inside submit, runs the URL executor in one thread, closes it in another, and proves close cannot return in the middle of submit/registration.
- [ ] Add a queued-task test with one occupied worker and one pending URL Future; close the executor and assert the queued fetch never starts.
- [ ] Add distinct pool-closed mapping coverage asserting `verifier_closed`, not `verifier_busy`.
- [ ] Run `test_ai4a3_resource_bounds.py` and record the expected race/mapping failures.
- [ ] Move pool submit and active registration under the executor state lock; keep cancellation outside the lock and preserve idempotent close.
- [ ] Split `UrlExecutionBusyError` and `UrlExecutionPoolClosedError` handlers.
- [ ] Run the resource-bound test file and adjacent interruptible URL tests to GREEN.

### Task 3: RED/GREEN Released Provider Handling

**Files:**
- Modify: `agent-server/tests/openhands_runtime/test_ai4a3_resource_bounds.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/url_evidence.py`

**Interfaces:**
- Consumes: global repository/fetcher/pool providers and `release_repository_provider()`.
- Produces: safe closed Observation without provider access.

- [ ] Add a test configuring all global providers, constructing an executor with `None` dependencies, calling `close()`, releasing providers, then invoking the action.
- [ ] Run that test and record the expected `RuntimeError` from repository lookup.
- [ ] Add an early `_closed_observation(action.evidence_id, started_at)` check before provider lookup; retain the atomic second check at submit.
- [ ] Run the provider regression plus normal missing/unsupported/source-URL tests to GREEN.

### Task 4: RED/GREEN Future TimeoutError Classification

**Files:**
- Modify: `agent-server/tests/openhands_runtime/test_ai4a3_resource_bounds.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/url_evidence.py`

**Interfaces:**
- Consumes: a completed `Future[FetchedUrl]` whose operation raises built-in `TimeoutError`.
- Produces: immediate safe `network_timeout` Observation while retaining <=10 ms polling for incomplete Futures.

- [ ] Add a fetcher with measurable `total_timeout_seconds` that immediately raises `TimeoutError` containing secret/path/query text; assert elapsed time is far below the deadline and serialized Observation contains none of it.
- [ ] Run the single test and record failure because current code waits to the deadline.
- [ ] In the `FutureTimeoutError` handler, continue only when `future.done()` is false; otherwise raise a new safe `UrlFetchError("network_timeout", "The URL request timed out.")` from the internal exception.
- [ ] Run resource-bound and interruptible deadline tests to GREEN and confirm no busy polling.

### Task 5: RED/GREEN Real Persistence Migration

**Files:**
- Modify: `agent-server/tests/openhands_runtime/test_ai4a3_evidence_context_migration.py`
- Modify only if the RED exposes a product defect: `agent-server/focusproof/openhands_runtime/synchronizer.py`

**Interfaces:**
- Consumes: stable `conversation_id`, identical `data_dir`/persistence path, `ConversationFactory.create()`, `ConversationSynchronizer.sync()`.
- Produces: one immutable old `evidence:{id}` event plus one idempotent `evidence-context:{id}:v1` event across a real close/restore.

- [ ] Seed SQLite Session and old text Evidence; create the first `LocalConversation`; write the old bodyless native message and retain its JSON.
- [ ] Synchronize once, assert the versioned context exists, then close the first Conversation and release its resources.
- [ ] Recreate with the same conversation ID, project root/data directory, and persistence path; assert `compatibility_restore is True`; synchronize again.
- [ ] Assert old JSON unchanged, exactly one context event, schema version 1, untrusted content, and no second audit fact; close the restored Conversation and release resources.
- [ ] Run the rewritten test before production changes. If it already passes, retain it as the required missing acceptance coverage rather than inventing a product change.

### Task 6: Report And Full Acceptance

**Files:**
- Modify: `docs/research/AI4A_GENERAL_VERIFICATION_FRAMEWORK_REPORT.md`

- [ ] Correct any unconditional claim that close prevents all new work; document the exact submit-first/close-first linearization semantics and bounded non-cooperative worker limitation.
- [ ] Record the real RED test names/failures and exact final outputs without inventing counts.
- [ ] Run:

```text
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_ai4a3_resource_bounds.py agent-server/tests/openhands_runtime/test_ai4a3_evidence_context_migration.py -q
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_interruptible_url_deadline.py agent-server/tests/openhands_runtime/test_message_synchronizer.py agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py -q
.venv/bin/python -m pytest agent-server/tests -q -m "not real_llm"
.venv/bin/ruff check agent-server
.venv/bin/mypy agent-server
git diff --check
git status --short --branch
```

- [ ] Verify Python 3.12, SDK 1.31.0, final changed paths, no prohibited paths, and unchanged protected-file hashes.
- [ ] Only after all gates pass, stage allowed AI4A.3/AI4A.3.1 runtime/tests/report/plan files and create local commits using `fix(runtime): make URL verification shutdown atomic` and `test(runtime): cover persisted evidence context migration` as appropriate. Do not push.
