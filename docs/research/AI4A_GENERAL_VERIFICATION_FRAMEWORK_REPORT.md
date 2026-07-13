# AI4A General Verification Framework Report

Date: 2026-07-13
Branch: `ai4a-general-verification-framework`
Baseline: `23a1a96460389147e6d477378f1d855a9a6a7187` (`main`)
OpenHands SDK: local path dependency, installed version `1.31.0`

## Outcome

AI4A extends the official OpenHands `LocalConversation` review runtime with an
immutable FocusProof capability catalog, deterministic per-session tool
assembly, repository-backed text verification, and bounded SSRF-aware URL
verification. It does not add a Conversation, EventLog, agent loop, or
executable tool protocol. The official API continues to use the existing
OpenHands Conversation path and deterministic FocusProof scoring remains outside
all tools.

## Changed Files By Responsibility

Capability policy and assembly:

- `agent-server/focusproof/openhands_runtime/capabilities.py`
- `agent-server/focusproof/openhands_runtime/tool_assembler.py`
- `agent-server/focusproof/openhands_runtime/tool_registry.py`
- `agent-server/focusproof/openhands_runtime/factory.py`
- `agent-server/focusproof/openhands_runtime/manager.py`

Verification contracts and executors:

- `agent-server/focusproof/openhands_runtime/tools/__init__.py`
- `agent-server/focusproof/openhands_runtime/tools/verification.py`
- `agent-server/focusproof/openhands_runtime/tools/text_evidence.py`
- `agent-server/focusproof/openhands_runtime/tools/url_safety.py`
- `agent-server/focusproof/openhands_runtime/tools/url_fetcher.py`
- `agent-server/focusproof/openhands_runtime/tools/url_evidence.py`

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
The Agent is still created with `include_default_tools=[]`. No terminal, file
editor, browser automation, patch, or workspace mutation tool is assembled.

The installed SDK 1.31.0 requires Conversation IDs to be UUIDs, which remains
handled by the existing stable UUID5 mapping. It also rejects restoration when
the runtime Agent removes any tool present in the persisted Agent. FocusProof
therefore preserves the allowlisted verifier superset when restoring persistent
conversations; adding tools is permitted, but narrowing is used only for fresh
factory-created conversations. A restart regression covers this difference. No
SDK source was patched.

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
event compatibility; it is absent from all new session tool assemblies.

## Per-Session Tool Assembly

Every session receives learner-input and review-draft control tools first.
Sessions without known evidence types receive both allowlisted text and URL
verifiers. Fresh conversations may narrow to known matching evidence types.
Persistent restoration retains the allowlisted verifier superset because the
installed SDK prohibits removing a tool that appeared in the persisted Agent.
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

## Text Verification

The text executor resolves authoritative evidence by `session_id + evidence_id`
and supports only `evidenceType == "text"`. Stable facts are `has_text`,
`character_count`, Unicode-aware `word_count`, `has_concrete_example`,
`has_structured_output`, and `content_hash`. Stable weak signals include
`text_too_short` and `generic_learning_claim`. Missing evidence and mismatched
types return safe typed observations. The verifier is deterministic, local,
read-only, and makes no LLM call or final learning judgment.

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

`BoundedUrlFetcher` requires an `httpx.Client` with automatic redirects disabled,
allows at most three explicitly revalidated redirects, maps connection/read
timeouts safely, rejects declared or streamed bodies over 1 MiB, and rejects
unsupported binary content. It retains only bounded title/plain-text metadata,
not raw page bodies. Successful URL facts include normalized/final URL,
hostname, status, content type/length, redirect chain, title, and excerpt.
Observation and audit URLs omit credentials, query strings, and fragments so
tokens cannot enter persisted source references or facts.

Policy violations map to failed observations, DNS and transient network
failures to inconclusive observations, unsupported content to unsupported
observations, and successful bounded retrieval to success. None assign learning
status or score.

## Prompt, EventLog, Projection, And Recovery

The prompt is capability-neutral: it tells the agent to use only tools exposed
in the current Conversation, pass evidence references rather than bodies, treat
failed/unsupported/inconclusive outcomes as limitations, ask one focused
question when necessary, and submit a score-free draft only when facts suffice.

Native `ActionEvent` remains before its matching `ObservationEvent`. The
projector recognizes the shared Action/Observation types, preserves native event
ID, index, tool call ID, tool name, and source references, and reconciliation
remains idempotent. Result extraction consumes only verification observations
after the latest answer boundary. Restart tests preserve Conversation ID,
native history, synchronized messages, audit uniqueness, and Review uniqueness.

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

## Verification Evidence

Baseline before implementation:

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

Full verification:

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

The warnings are the existing Starlette/httpx TestClient deprecation and Python
3.12 SQLite datetime-adapter deprecations. No real-LLM test was run because AI0
did not explicitly authorize it.

## Known Limitations And Deferred Work

- Text analysis is deterministic heuristic metadata, not semantic proof.
- URL extraction supports bounded textual metadata only; binary documents,
  OCR, ASR, video, PDF, and browser automation are deferred.
- URL verification records bounded textual metadata and policy outcomes, but
  does not retain low-level transport telemetry beyond those diagnostics.
- Toolset version mismatch is surfaced diagnostically; it does not rewrite
  historical native events or persisted Agent state.
- Code execution, Web3 RPC, multimodal ingestion, contracts, deployment, and all
  AI4B work remain deferred.

## Scope And Protocol Status

No public architecture, protocol, task-board, design, or plan document changed.
No file under `frontend/`, `contracts/`, `.env`, `var/`, or the OpenHands SDK
source changed. No secret was read, printed, edited, or committed. No remote
branch was created or updated, and no push or merge was performed.
