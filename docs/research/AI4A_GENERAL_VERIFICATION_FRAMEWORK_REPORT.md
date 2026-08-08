# AI4A General Verification Framework Report

Date: 2026-07-14
Branch: `ai4a-general-verification-framework`
AI4A baseline: `23a1a96460389147e6d477378f1d855a9a6a7187` (`main`)
AI4A.1 starting HEAD: `7a93546ca3963a5e934f9bbb6a2ff03cea9028ee`
AI4A.2 starting HEAD: `00e32722d3550a05e0c45852ef96dbc0e47af281`
OpenHands SDK: local path dependency, installed version `1.31.0`

## Outcome

AI4A extends the official OpenHands `LocalConversation` review runtime with an
immutable FocusProof capability catalog, deterministic per-session tool
assembly, repository-backed text verification, and bounded SSRF-aware URL
verification. It does not add a Conversation, EventLog, agent loop, or
executable tool protocol. The official API continues to use the existing
OpenHands Conversation path and deterministic FocusProof scoring remains outside
all tools.

AI4A.1 closes AI0's upgrade-compatibility and security findings. It restores
real pre-AI4A SDK state with an additive tool superset, applies one
capability-driven monotonic deadline to complete URL verification, removes URL
secrets at every native/product projection boundary, and hardens both the Agent
prompt and Observation schema against untrusted verdict instructions.

AI4A.2 closes the acceptance gaps in hard URL deadlines, restored-event
projection order, and Agent-visible text semantics. It retains the same native
OpenHands runtime and tool/event contracts.

## Changed Files By Responsibility

Capability policy and assembly:

- `agent-server/focusproof/openhands_runtime/capabilities.py`
- `agent-server/focusproof/openhands_runtime/tool_assembler.py`
- `agent-server/focusproof/openhands_runtime/tool_registry.py`
- `agent-server/focusproof/openhands_runtime/factory.py`
- `agent-server/focusproof/openhands_runtime/handle.py`
- `agent-server/focusproof/openhands_runtime/manager.py`
- `agent-server/focusproof/openhands_runtime/synchronizer.py`
- `agent-server/focusproof/openhands_runtime/evidence_messages.py`

Verification contracts and executors:

- `agent-server/focusproof/openhands_runtime/tools/__init__.py`
- `agent-server/focusproof/openhands_runtime/tools/verification.py`
- `agent-server/focusproof/openhands_runtime/tools/text_evidence.py`
- `agent-server/focusproof/openhands_runtime/tools/url_safety.py`
- `agent-server/focusproof/openhands_runtime/tools/url_fetcher.py`
- `agent-server/focusproof/openhands_runtime/tools/url_evidence.py`
- `agent-server/focusproof/openhands_runtime/url_redaction.py`

Native event consumption and scoring boundary:

- `agent-server/focusproof/openhands_runtime/prompts.py`
- `agent-server/focusproof/openhands_runtime/projector.py`
- `agent-server/focusproof/openhands_runtime/result_extractor.py`
- `agent-server/focusproof/domain/scoring.py`

Tests:

- capability, shared-contract, text, URL policy/fetch/tool, assembler, factory,
  registry lifecycle, native event, projection, scoring, and restart recovery
  tests under `agent-server/tests/`.

No dependency declaration changed. `httpx` was already a direct project
dependency.

## OpenHands SDK Types Used Directly

The implementation continues to use OpenHands `Agent`, `Conversation`,
`LocalConversation`, `ConversationState`, the native EventLog exposed through
`conversation.state.events`, `Tool`, `ToolDefinition`, `ToolExecutor`, `Action`,
`Observation`, `MessageEvent`, `ActionEvent`, and `ObservationEvent` directly.
Review execution now calls `LocalConversation.arun()`; interruption remains
native through `LocalConversation.interrupt()`, its public `cancel_token`, and
the SDK calls to `ToolExecutor.interrupt()` and `ToolExecutor.close()`.
The Agent is still created with `include_default_tools=[]`. No terminal, file
editor, browser automation, patch, or workspace mutation tool is assembled.

The installed SDK 1.31.0 requires Conversation IDs to be UUIDs, which remains
handled by the existing stable UUID5 mapping. It also rejects restoration when
the runtime Agent removes any tool present in the persisted Agent. FocusProof
therefore preserves the allowlisted verifier superset when restoring persistent
conversations. SDK registry registration alone is insufficient because
`Agent.verify()` compares persisted explicit tool names with the new Agent
specification before tool construction. The factory detects the exact SDK
`base_state.json` through `LocalConversation.get_persistence_dir()` and supplies
the legacy tool name to the restoring Agent. A real legacy native-state fixture
proves event IDs and JSON remain unchanged across two restores. No SDK source
was patched.

## Capability Registry And SDK Registry Boundary

`VerificationCapability` records stable capability name, OpenHands tool class,
supported evidence types and domains, priority, read-only/network policy,
timeout, enabled state, and version. `VerificationCapabilityRegistry` provides
thread-safe idempotent registration, conflicting-duplicate rejection, filtering,
stable priority/name ordering, and cleanup.

Built-in capabilities are:

- `text` -> `FocusProofTextEvidenceVerificationTool`, priority 10, version 1;
- `url` -> `FocusProofUrlEvidenceVerificationTool`, priority 20, version 1.

The OpenHands SDK registry remains responsible for constructing executable tool
definitions. The FocusProof registry only selects policy metadata. The legacy
monolithic verification class remains registered solely for historical SDK
event compatibility. It is absent from fresh session tool assemblies and added
only when restoring an existing SDK state that requires its explicit name.

## Per-Session Tool Assembly

Every session receives learner-input and review-draft control tools first. The
actual fresh default names are `focusproof_learner_input`,
`focusproof_review_draft`, `focusproof_text_evidence_verification`, and
`focusproof_url_evidence_verification`. Fresh conversations may narrow to known
matching evidence types. Restored sessions receive the current tools plus
`focusproof_evidence_verification`, the legacy verifier required by the
persisted Agent compatibility check.
Every serialized `Tool.params` value contains only the trusted `session_id`;
repositories, resolvers, clients, credentials, bodies, and paths are never
serialized for the LLM.

The toolset version is the first 12 hexadecimal characters of SHA-256 over
sorted selected capability `name:version` pairs. It is deterministic across
input ordering and changes when the selected capability set changes. The
factory records it in native Conversation tags and exposes both current and
persisted values, plus an explicit mismatch flag, through `ConversationHandle`.
This remains diagnostic metadata rather than a second runtime truth source.

The application-owned provider now owns both the evidence repository and URL
fetcher. The existing `release_repository_provider()` lifespan cleanup releases
both and closes the owned HTTP client, so the FastAPI app required no change.

## Shared Verification Contract

`EvidenceReferenceAction` contains only `evidence_id`; `session_id` remains a
trusted server parameter. `VerificationObservation` is an OpenHands-native
Observation subtype with:

- `evidence_id`, `capability`, and status;
- structured facts, weak signals, and source references;
- verifier version and UTC start/completion timestamps;
- optional stable error code and safe error message.

Its status is one of `success`, `failed`, `inconclusive`, or `unsupported`.
There are no score, final status, `verified_learning`, character, honesty,
effort, or worth verdict fields.

Before Pydantic converts field types, a recursive validator traverses mappings
and sequences in both `facts` and raw `weak_signals`. It rejects `score`,
`final_score`, `learning_status`, `verified_learning`, `honest`, `dishonest`,
and `fake_learning` at any nesting depth. Built-in verifier outputs remain
valid, while an Observation cannot smuggle a tool-authored final verdict.

## Text Verification

The text executor resolves authoritative evidence by `session_id + evidence_id`
and supports only `evidenceType == "text"`. Stable facts are `has_text`,
`character_count`, Unicode-aware `word_count`, `has_concrete_example`,
`has_structured_output`, and `content_hash`. Stable weak signals include
`text_too_short` and `generic_learning_claim`. Missing evidence and mismatched
types return safe typed observations. The verifier is deterministic, local,
read-only, and makes no LLM call or final learning judgment.

Agent-facing evidence ingestion is intentionally separate from the audit-safe
projection. `safe_evidence_payload()` remains unchanged and continues to omit
bodies. `runtime_evidence_payload()` adds text only to a native user
`MessageEvent`, labels it `contentTrust: untrusted`, applies the SDK's
`redact_text_secrets()`, caps it at 4,000 characters, and records
`textTruncated` plus `originalCharacterCount`. Persistent and legacy ingestion
use the same builder. Authoritative database text is unchanged, URL messages
still contain only origin/hash metadata, and no `extended_content` is used.

## URL Safety And Retrieval

Production permits HTTPS only; HTTP is available only through explicit policy
configuration for controlled fixtures. The policy rejects credentials,
unsupported schemes, localhost, literal or resolved loopback/private/link-local/
multicast/unspecified/reserved IPv4 and IPv6 addresses, and metadata targets.
Fragments are stripped, scheme/host are normalized, and DNS policy is applied
immediately before the first request and again before every redirect request.
Each request connects to the policy-validated IP while retaining the original
hostname for HTTP Host and TLS SNI, closing the DNS validation/connection race.
The production client disables HTTP/2 and keep-alive pooling; verifier requests
also request connection close and remove Cookie headers so hostnames sharing an
IP cannot reuse a TLS connection or cookie state.

`BoundedUrlFetcher` requires an `httpx.Client` with automatic redirects disabled
and an explicit total timeout copied from URL capability metadata. Its
monotonic budget still propagates remaining time to HTTPX and cooperatively
checks policy, redirects, streamed chunks, decoding, and extraction. AI4A.2
adds the missing hard wall-clock boundary at the URL `ToolExecutor`: each call
runs blocking DNS/transport work in an isolated daemon worker, waits only for
the capability budget, and immediately returns `network_timeout` /
`inconclusive` on expiry. An operation-local interrupt event stops cooperative
work when possible and otherwise isolates the lingering blocking operation.
`interrupt()` and `close()` are thread-safe and idempotent and never close the
shared client, so a timeout in one session cannot affect another. Redirect,
size, DNS pinning, and SSRF rules are unchanged.

The only URL representation permitted in an Observation, LLM message, source
reference, or audit projection contains scheme, hostname, optional non-default
port, origin, `path_redacted`, and SHA-256 of the full canonical URL. It never
contains path, query, fragment, or userinfo. URL-like source references become
`url-sha256:<digest>`, and URL tokens echoed by title/excerpt extraction become
`[redacted]`. Old persisted messages and legacy Observation projection use the
same read-only sanitizer. The database retains the original URL and the URL
executor passes it only to the bounded fetcher.

Policy violations map to failed observations, DNS and transient network
failures to inconclusive observations, unsupported content to unsupported
observations, and successful bounded retrieval to success. None assign learning
status or score.

## Prompt, EventLog, Projection, And Recovery

The prompt is capability-neutral: it tells the agent to use only tools exposed
in the current Conversation, pass evidence references rather than bodies, treat
failed/unsupported/inconclusive outcomes as limitations, ask one focused
question when necessary, and submit a score-free draft only when facts suffice.
It explicitly classifies evidence text/excerpts as untrusted content, rejects
embedded commands/tool calls/system prompts/scoring instructions, and states
that no Observation directly determines the final score.

Native `ActionEvent` remains before its matching `ObservationEvent`. The
projector recognizes current and legacy native types, preserves identity and
ordering metadata, converts legacy results without mutating native JSON, maps
legacy `verified` to no verdict, and remains idempotent. Result extraction maps
legacy verification to `inconclusive` and consumes only observations after the
latest answer boundary. Restart tests preserve Conversation ID, native history,
synchronized messages, audit uniqueness, and Review uniqueness.

On create/restore, the manager now snapshots `conversation.state.events` and
reconciles that native history before synchronization can emit a new callback.
The projector therefore advances to the restored native length first. New
events retain their true native indices, old audit rows precede new rows, and
the existing source-event and `message_key` idempotence mechanisms remain the
only crash-window recovery path; callbacks are not replayed and no second
EventLog exists.

## Domain-General Scoring

Generic scoring no longer contains nonce, gas, transaction, wallet, chain, or
block-explorer concept rewards and no longer has a transaction-shaped shortcut.
It uses domain-neutral text specificity, overlap with meaningful goal terms,
and learner answers while preserving the existing dimensions, score bounds,
public response fields, and weak-evidence behavior. URL-only evidence is no
longer treated as an empty generic-text set, generic notes do not override a
Unicode-aware specific answer or successful verifier fact, and trivial answers
do not receive understanding credit. CJK specificity uses Unicode-aware lexical
units. Successful verification observations remain supporting facts and cannot
set `VerifiedLearning`.

## Root Causes

- URL timeout checks surrounded blocking DNS and streamed reads but could not
  make a blocked synchronous phase return at the total deadline. The URL
  executor discarded its `conversation` argument and supplied no lifecycle
  hooks, while the manager used synchronous `LocalConversation.run()`.
- Restore synchronized pending database facts before reconciling restored
  native history. The new callback projector therefore began at zero and gave
  newly emitted messages indices that overlapped older native events.
- `safe_evidence_payload()` was incorrectly reused as the Agent-facing text
  message. Its correct privacy behavior drops bodies, so the Agent received
  identifiers and structural verifier facts but not the submitted semantics.

## OpenHands APIs Reused

- `LocalConversation.arun()`, `LocalConversation.interrupt()`, and the public
  `LocalConversation.cancel_token` own run cancellation.
- The existing SDK `ToolExecutor.interrupt()` and `ToolExecutor.close()` hooks
  signal the session-local URL executor; no FocusProof cancellation token was
  introduced.
- `conversation.state.events`, native `MessageEvent.llm_message`,
  `MessageEvent.to_llm_message()`, `ActionEvent`, `ObservationEvent`, and the
  existing native EventLog remain the only runtime/event truth.
- The SDK's `redact_text_secrets()` performs Agent-message secret redaction.
- `Agent`, `LocalConversation`, `ToolDefinition`, and `ToolExecutor` remain
  directly instantiated/implemented, with OpenHands default programming tools
  disabled.

## FocusProof-Owned SDK Gaps

OpenHands SDK 1.31.0 provides conversation cancellation and thread-safe tool
interrupt hooks, but it does not provide a single-tool hard wall-clock deadline
for a synchronous `ToolExecutor`. Cancelling the async wrapper cannot terminate
an already blocked worker thread. FocusProof therefore owns only a minimal URL
call deadline adapter: one daemon worker per call, one wall-clock wait bounded by
the capability timeout, and one operation-local interrupt event. It is not a
Conversation, runtime, agent loop, EventLog, cancellation-token abstraction, or
tool protocol. Blocking work that cannot be force-stopped is isolated until the
OS/library call returns; the shared client remains open.

## Security/Privacy Verification

- Resolver delay and slow-drip tests prove a 50 ms budget returns within the
  stated tolerance with `network_timeout` and `inconclusive`.
- Repeated executor interrupt/close calls are safe, and a timed-out Session A
  leaves Session B's shared client usable.
- Timeout Observation and projected audit JSON contain no URL credentials,
  private path, query value, or fragment. URL Agent messages retain only
  origin/hash metadata and tool action arguments remain only `evidence_id`.
- Text is persisted to native user messages only after the SDK secret redactor
  and the 4,000-character cap. Prompt-like text remains visibly untrusted user
  content. Raw authoritative text remains in the database; audit projection
  still omits all evidence bodies.
- Restore tests prove native-index ordering, source ID uniqueness, no duplicate
  audit/Review/message rows, and marker-only repair when the native message
  exists but the database sync marker is missing.

## Verification Evidence

Original AI4A baseline before AI4A implementation:

```text
.venv/bin/python -m pytest agent-server/tests -q -m "not real_llm"
103 passed, 1 deselected, 8 warnings
```

Task-focused TDD captured missing-module/old-assumption red states before each
production behavior. Final focused and static results included:

```text
Task 1 registry: 4 passed; Ruff passed; Mypy 2 files passed
Task 2 contract: 3 passed; Ruff passed; Mypy 1 file passed
Task 3 text/tool execution: 9 passed; Ruff passed; Mypy 1 file passed
Task 4 URL safety/tool: 28 passed; Ruff passed; Mypy 3 files passed
Task 5 assembly/factory/registry/restart: 16 passed; Ruff passed; Mypy 21 files passed
Task 6 native flow/projection/lifecycle: 12 passed; Ruff passed; Mypy 21 files passed
Task 7 scoring/API: 13 passed, 1 warning; Ruff passed; Mypy 5 files passed
Final review hardening (URL safety/redaction/isolation, scoring, CJK, toolset
diagnostics): 65 passed
```

Original AI4A verification before the AI4A.1 closure:

```text
.venv/bin/python -m pytest agent-server/tests -q -m "not real_llm"
168 passed, 1 deselected, 8 warnings

.venv/bin/ruff check agent-server
All checks passed!

.venv/bin/mypy agent-server
Success: no issues found in 111 source files

.venv/bin/python -m pytest agent-server/tests/persistence agent-server/tests/api/test_restart_persistence.py agent-server/tests/openhands_runtime -q -m "not real_llm"
116 passed, 1 deselected, 8 warnings
```

AI4A.1 strict TDD recorded these expected failures before production changes:

```text
Legacy restore RED: RuntimeCreationError caused by SDK "tools were removed mid-conversation"
URL deadline RED: no total budget/registry lookup and stream continued past deadline
URL redaction RED: 7 failures across Observation, message, manager, and audit paths
Trust-boundary RED: 9 failures for nested verdict keys and missing prompt rules
```

Fresh AI4A.1 acceptance evidence:

```text
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_upgrade_compatibility.py -q
2 passed

.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_url_safety.py agent-server/tests/openhands_runtime/test_url_evidence_tool.py -q
37 passed

.venv/bin/python -m pytest agent-server/tests/persistence agent-server/tests/api/test_restart_persistence.py agent-server/tests/openhands_runtime -q -m "not real_llm"
147 passed, 1 deselected, 8 warnings

.venv/bin/python -m pytest agent-server/tests -q -m "not real_llm"
199 passed, 1 deselected, 8 warnings

.venv/bin/ruff check agent-server
All checks passed!

.venv/bin/mypy agent-server
Success: no issues found in 113 source files
```

## Commands And Exact Results

AI4A.2 wrote and ran three independent RED groups before production changes:

```text
test_interruptible_url_deadline.py: 4 failed, 3 passed
test_restore_projection_order.py: 1 failed, 1 passed
test_runtime_evidence_messages.py: 5 failed, 1 passed
```

The failures were the expected 200 ms wall-clock overrun and synchronous
`run()`, duplicate/out-of-order restored source indices, and absent text fields
in native messages. Final independent groups:

```text
.venv/bin/pytest -q agent-server/tests/openhands_runtime/test_interruptible_url_deadline.py
7 passed in 0.64s

.venv/bin/pytest -q agent-server/tests/openhands_runtime/test_restore_projection_order.py
2 passed in 0.90s

.venv/bin/pytest -q agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py
6 passed in 1.17s
```

AI4A.2 final suite and static verification:

```text
.venv/bin/pytest -q -m 'not real_llm'
214 passed, 1 deselected, 8 warnings in 19.56s

.venv/bin/ruff check agent-server
All checks passed!

.venv/bin/mypy agent-server/focusproof
Success: no issues found in 68 source files

git diff --check 00e32722d3550a05e0c45852ef96dbc0e47af281..HEAD
exit 0
```

No model, migration, or schema file changed from the AI4A.2 baseline, so
Alembic upgrade/down/re-upgrade is explicitly unaffected rather than rerun.
The implementation path list contains only `agent-server/focusproof/` runtime
code and `agent-server/tests/`; the final documentation commit adds only this
report and the AI4A.2 plan. The six AI0 normative working-tree files retain
their pre-work SHA-256 hashes and remain unstaged. No frontend, contracts,
`.env`, `var`, key, or OpenHands SDK source path changed.

The warnings are the existing Starlette/httpx TestClient deprecation and Python
3.12 SQLite datetime-adapter deprecations. No real-LLM test was run because AI0
did not explicitly authorize it.

## AI4A.3 Migration And Resource Bound Repair

AI4A.3 closes the two remaining acceptance gaps without introducing a second
Agent loop, EventLog, Conversation implementation, or tool protocol:

- URL tools still execute through the OpenHands SDK `ToolDefinition`,
  `ToolExecutor`, native Action/Observation events, Conversation cancellation,
  and executor `interrupt()`/`close()` lifecycle. SDK 1.31.0 does not provide a
  global hard wall-clock boundary for a blocking synchronous executor, so the
  application owns one bounded isolation pool with four workers and four queued
  calls. Capacity exhaustion is rejected immediately as an inconclusive tool
  Observation; it cannot create an unbounded thread or queue.
- Executor close and submit now have an explicit ordering boundary: an open
  check, bounded-pool submit, and active-Future registration form one short
  state-lock critical section. If close linearizes first, no provider is read
  and no Future is submitted. If submit linearizes first, close signals the
  registered operation and cancels it while still queued when possible. Close
  never holds the state lock while waiting for network work. Already-running
  non-cooperative library calls cannot be killed by Python threads, but their
  number is permanently bounded by the worker count.
- Historical OpenHands events remain immutable. When restore finds an old
  bodyless `evidence:{id}` text message, synchronization appends exactly one
  versioned `evidence-context:{id}:v1` native user message containing bounded,
  redacted, explicitly untrusted text. Repeated synchronization is idempotent.
  The context event is intentionally ignored by the FocusProof audit projector,
  so it does not create a second product fact.
- Full-project Mypy is restored as an acceptance gate instead of checking only
  a production subtree.

Fresh AI4A.3 verification:

```text
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_ai4a3_resource_bounds.py agent-server/tests/openhands_runtime/test_ai4a3_evidence_context_migration.py -q
5 passed

.venv/bin/python -m pytest agent-server/tests -q -m "not real_llm"
219 passed, 1 deselected, 8 warnings

.venv/bin/ruff check agent-server
All checks passed!

.venv/bin/mypy agent-server
Success: no issues found in 120 source files

git diff --check
exit 0
```

No database model, migration, public API, frontend, contract, `.env`, `var`, or
OpenHands SDK source changed in AI4A.3. The six AI0 normative files remain
outside this phase's patch and retain their existing uncommitted state.

## AI4A.3.1 Lifecycle Atomicity Repair

### RED Evidence And Root Causes

The regression tests were run individually before their production fixes:

```text
test_executor_close_linearizes_with_submit_and_cancels_queued_fetch
FAIL: close returned while GatePool.submit() was still paused

test_closed_execution_pool_maps_to_verifier_closed_not_busy
FAIL: expected verifier_closed, received verifier_busy

test_closed_executor_after_provider_release_returns_safe_observation
FAIL: get_repository_provider() raised RuntimeError after provider release

test_completed_future_timeout_error_maps_immediately_and_safely
FAIL: immediate task TimeoutError returned after 0.318s, exceeding the 0.1s bound
```

The lifecycle race came from submitting to the pool before acquiring the
executor state lock and registering the Future. Provider resolution likewise
preceded the first closed-state check. Finally, Python 3.12 exposes
`concurrent.futures.TimeoutError` as the built-in `TimeoutError`, so the wait
timeout handler also caught a completed task's own exception and continued
polling until the total deadline. The real close/restore migration test passed
on its first run; that acceptance gap was missing lifecycle coverage rather
than a synchronizer defect.

### Lifecycle State Transitions

- **Open -> submitted/registered:** under `_state_lock`, recheck open, call the
  non-blocking bounded-pool submit, and register the returned Future.
- **Open -> closed:** under `_state_lock`, mark closed and snapshot active calls;
  outside the lock, set each operation event and cancel its Future.
- **Closed -> call:** return an inconclusive `verifier_closed` Observation using
  only `action.evidence_id`; do not resolve any global provider.
- **Submitted -> close:** a queued Future is cancelled before fetch begins; a
  running cooperative fetch observes interruption; a running non-cooperative
  thread remains bounded by the application-wide worker limit.
- Pool capacity exhaustion maps only to `verifier_busy`; executor or pool
  shutdown maps only to `verifier_closed`. Repeated close remains idempotent.

### OpenHands Reuse And FocusProof Supplement

OpenHands SDK 1.31.0 remains the direct owner of `Agent`, `LocalConversation`,
native EventLog state, `ToolDefinition`, `ToolExecutor`, Action/Observation
events, and executor `interrupt()` / `close()` calls. Evidence migration uses
native `MessageEvent` plus `LocalConversation.send_message()` and persistence;
it never mutates Conversation state or creates another event stream.

The only FocusProof supplement is the existing bounded URL execution pool and
its executor-local atomic bookkeeping. It is necessary because SDK 1.31.0 has
no application-wide hard bound for synchronous blocking I/O threads or queued
calls. It does not dispatch tools or run an Agent loop. Python cannot forcibly
terminate a non-cooperative running thread; the pool only makes that exposure
finite. Waiting remains interruptible in at most 10ms increments. A
`FutureTimeoutError` continues polling only when `future.done()` is false; when
the Future is done, the task's own TimeoutError is immediately replaced with a
safe `network_timeout` Observation.

### Persistence Migration Evidence

The migration test now seeds SQLite, creates a `LocalConversation`, writes the
old bodyless `evidence:ev_old` MessageEvent, synchronizes the versioned context,
saves the old JSON, closes the Conversation and releases providers, then
recreates the Conversation with the same ID and data/persistence roots. The
restored handle reports `compatibility_restore=True`. A second synchronization
leaves the old JSON byte-for-byte unchanged, retains exactly one
`evidence-context:ev_old:v1` event with `contextSchemaVersion=1` and
`contentTrust=untrusted`, and produces no second `evidence.submitted` audit
fact.

### AI4A.3.1 Exact Verification

```text
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_ai4a3_resource_bounds.py agent-server/tests/openhands_runtime/test_ai4a3_evidence_context_migration.py -q
9 passed in 0.62s

.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_interruptible_url_deadline.py agent-server/tests/openhands_runtime/test_message_synchronizer.py agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py -q
17 passed in 1.92s

.venv/bin/python -m pytest agent-server/tests -q -m "not real_llm"
223 passed, 1 deselected, 8 warnings in 14.81s

.venv/bin/ruff check agent-server
All checks passed!

.venv/bin/mypy agent-server
Success: no issues found in 120 source files

git diff --check
exit 0
```

AI4A.3/AI4A.3.1 changes are restricted to runtime code, runtime tests, this
research report, and the implementation plan. No database model or Alembic
file changed. The six AI0 normative files remain unstaged with their preserved
pre-work SHA-256 values: `baa155...`, `73dd59...`, `b814df...`, `7855c5...`,
`6c49cd...`, and `2c87e0...` respectively.

## Remaining Limitations

- Text analysis is deterministic heuristic metadata, not semantic proof.
- A DNS or transport library call that cannot cooperate with interruption may
  continue in a bounded pool worker after the caller receives the hard timeout.
  Python cannot forcibly kill that thread, but the application-wide worker and
  queue limits prevent unbounded resource growth.
- URL extraction supports bounded textual metadata only; binary documents,
  OCR, ASR, video, PDF, and browser automation are deferred.
- URL verification records bounded textual metadata and policy outcomes, but
  exposes only origin-level diagnostics plus a canonical-URL digest; raw URL
  recovery from runtime/audit output is intentionally impossible.
- Toolset version mismatch is surfaced diagnostically; it does not rewrite
  historical native events or persisted Agent state.
- Code execution, Web3 RPC, multimodal ingestion, contracts, deployment, and all
  AI4B work remain deferred.

## Scope And Protocol Status

No public architecture, protocol, project-management, task-board, or design
document changed. The implementation plan under `docs/superpowers/plans/` and
this research report are the only documentation updates.
No file under `frontend/`, `contracts/`, `.env`, `var/`, or the OpenHands SDK
source changed. No secret was read, printed, edited, or committed. No remote
branch was created or updated, and no push or merge was performed.
