# AI4A General Verification Framework Design

Status: Approved design
Date: 2026-07-13
Owner: AI0 architecture, AI4A implementation
Runtime: Python Agent Server with OpenHands SDK

## 1. Purpose

AI4A turns the current fixed, monolithic evidence checker into an extensible
verification framework for general learning evidence. It does not replace the
OpenHands runtime and it does not create a second agent loop, tool protocol, or
event ledger.

The first implementation supports text and URL evidence. Code, Web3 RPC,
images, audio, video, and PDF processing remain future capabilities behind the
same interfaces.

## 2. Current State

The official review path already uses:

- OpenHands `Agent` and `LocalConversation`;
- `ConversationState` and the native OpenHands EventLog;
- native `Action`, `Observation`, `ToolDefinition`, and `ToolExecutor` types;
- `ActionEvent -> ToolExecutor -> ObservationEvent` ordering;
- FocusProof product projections, persistence, ownership checks, and recovery;
- independent FocusProof scoring after the agent submits a review draft.

The current limitations are intentionally narrow MVP choices:

- `ConversationFactory._session_tools()` always exposes three FocusProof tools;
- `FocusProofEvidenceVerificationTool` handles both general text and
  transaction-shaped evidence;
- the system prompt says that exactly three tools exist;
- the registry registers SDK tool classes but has no capability metadata or
  per-session selection policy;
- general scoring contains Web3-specific vocabulary and transaction branches.

These are extension boundaries, not reasons to rewrite the runtime.

## 3. Chosen Approach

Use independent OpenHands-native verification tools plus a FocusProof-owned
capability registry and deterministic per-session tool assembly.

Rejected alternatives:

1. Keep one universal verification tool with an internal evidence-type switch.
   This minimizes the first diff but creates a growing conditional executor,
   hides capabilities from the agent, and couples unrelated security policies.
2. Load external Python plugin packages dynamically. This is more extensible
   than the product currently needs and adds package trust, versioning, and
   deployment complexity before the core tool contract is stable.

## 4. Architectural Boundaries

The runtime flow remains:

```text
FocusProof API
  -> product repository writes goal/evidence/answer
  -> ConversationSynchronizer sends stable reference messages
  -> OpenHands LocalConversation.run()
  -> Agent.step() selects an exposed FocusProof tool
  -> native ActionEvent
  -> OpenHands ToolExecutor
  -> native ObservationEvent
  -> OpenHands EventLog and next ConversationState view
  -> FocusProof review-draft extraction
  -> independent deterministic FocusProof scoring
  -> product/audit projection
```

AI4A must not introduce:

- a local replacement for OpenHands Conversation or EventLog;
- a custom action loop beside `LocalConversation.run()`;
- a second executable tool protocol;
- a parallel runtime truth store;
- final scores or learning verdicts inside tool observations;
- direct LLM access to repository credentials or unrestricted evidence text in
  tool arguments.

## 5. Tool Categories

The framework distinguishes three tool categories:

### 5.1 Runtime Control Tools

- `FocusProofLearnerInputTool` asks one focused follow-up question.
- `FocusProofReviewDraftTool` submits structured findings without a score.

These tools are available to every review session.

### 5.2 General Verification Tools

- `FocusProofTextEvidenceVerificationTool`
- `FocusProofUrlEvidenceVerificationTool`

These tools inspect evidence through repository references and return facts.

### 5.3 Future Domain Tools

Examples include code classifiers, programming sandboxes, Web3 receipt readers,
OCR, ASR, and PDF parsers. They are not implemented in AI4A. Their future
addition must not require changes to the Conversation loop.

OpenHands default programming tools remain disabled. `TerminalTool`,
`FileEditorTool`, browser automation, patch tools, and workspace mutation tools
must not be enabled by this phase.

## 6. Capability Model

FocusProof owns immutable capability metadata. A capability describes an
OpenHands `ToolDefinition`; it does not execute the tool.

Required fields:

- `registry_name`: stable FocusProof capability identifier;
- `tool_class_name`: registered OpenHands SDK tool class name;
- `supported_evidence_types`: non-empty normalized set;
- `supported_domains`: normalized set, with `*` for domain-general tools;
- `priority`: deterministic integer ordering key;
- `read_only`: must be true for AI4A verification tools;
- `requires_network`: distinguishes local text analysis from URL retrieval;
- `timeout_seconds`: positive bounded execution budget;
- `enabled`: deployment-level availability switch;
- `version`: observation and recovery compatibility identifier.

`VerificationCapabilityRegistry` must support:

- idempotent initialization of the built-in capability set;
- rejection of conflicting duplicate names;
- deterministic lookup by evidence type and domain;
- exclusion of disabled capabilities;
- stable ordering by priority and registry name;
- thread-safe reads and writes during application startup and shutdown;
- explicit cleanup in the FastAPI lifespan without mutating OpenHands private
  registry state.

The existing OpenHands `register_tool()` registry remains the SDK factory for
constructing tools. The FocusProof capability registry is a policy catalog over
those registered classes, not a replacement for the SDK registry.

## 7. Per-Session Tool Assembly

`ConversationFactory` continues to create the OpenHands `Agent`. It delegates
tool selection to a focused assembler rather than hardcoding tool names.

Inputs:

- session ID;
- learning domain;
- evidence types currently stored for the session;
- enabled capability catalog;
- repository/provider references required by tool factories.

Output:

- a stable list of OpenHands `Tool` specifications;
- always includes learner-input and review-draft control tools;
- includes only matching enabled verification capabilities;
- never includes OpenHands default tools;
- records a deterministic toolset version for diagnostics and recovery.

A session created before evidence exists may expose the two general AI4A
verification tools, because both are read-only and selected from an explicit
FocusProof allowlist. After evidence exists, the assembler narrows the list to
matching evidence types. This avoids requiring a Conversation replacement when
the first evidence is submitted while still preventing unrelated future domain
tools from being exposed globally.

Restored conversations use their persisted OpenHands event history as runtime
truth. A toolset version mismatch is reported for diagnostics, but does not
rewrite past ActionEvents or ObservationEvents.

## 8. Repository Reference Rule

Verification actions contain identifiers, not authoritative evidence bodies.

Required action fields:

- `evidence_id`;

Session identity is injected by the trusted server-side `Tool` factory params.
The LLM must not supply or override `session_id`.

The executor resolves `session_id + evidence_id` through
`SessionEvidenceRepository`. It must reject missing evidence, evidence belonging
to another session, and unsupported evidence types with structured safe errors.

This rule prevents prompt-injected evidence text from becoming the tool's source
of truth.

## 9. Unified Verification Observation

All AI4A verification tools return a shared semantic envelope through their
OpenHands-native Observation subtype.

Required fields:

- `evidence_id`;
- `capability`;
- `status`: `success`, `failed`, `inconclusive`, or `unsupported`;
- `facts`: structured observed facts only;
- `weak_signals`: evidence limitations that require interpretation;
- `source_refs`: evidence and external-source references;
- `verifier_version`;
- `started_at` and `completed_at` in UTC;
- optional `error_code`;
- optional `safe_error_message`.

Observations must not contain:

- a final numeric score;
- a final learning status;
- `verified_learning` or an equivalent verdict;
- judgments about learner character, honesty, effort, or worth;
- secrets, database URLs, internal filesystem paths, or raw exception traces.

An `inconclusive` observation means the verifier could not establish a fact. It
does not mean that the evidence is false.

## 10. Text Verification

The text verifier performs deterministic local analysis. It does not call an
LLM and does not determine whether learning occurred.

It observes:

- whether text exists;
- Unicode-safe character and token/word counts;
- whether the text is below configurable specificity thresholds;
- generic learning-claim phrases;
- concrete examples, structured sections, and output-like patterns;
- content hash and evidence reference consistency.

The verifier returns weak signals for short or generic text. Domain-specific
terminology must not be embedded in the general verifier. Understanding remains
the responsibility of follow-up questions and final scoring.

## 11. URL Verification And SSRF Safety

The URL verifier separates normalization, network policy, retrieval, and content
extraction so each part can be tested independently.

Allowed schemes:

- `https`;
- `http` only when explicitly enabled for local development fixtures.

Always reject:

- embedded credentials;
- unsupported schemes including `file`, `ftp`, `data`, and `javascript`;
- localhost names and loopback addresses;
- private, link-local, multicast, unspecified, and reserved IPv4/IPv6 ranges;
- cloud metadata addresses;
- hostnames that resolve to a blocked address;
- redirects whose next target fails the same policy;
- redirect chains over the configured limit;
- responses over the configured byte limit;
- connection and read times beyond configured timeouts.

DNS resolution and every redirect target must be checked immediately before the
corresponding request. The HTTP client must not automatically follow redirects
without policy revalidation.

The verifier may return:

- normalized URL and hostname;
- response status;
- content type and bounded content length;
- redirect chain;
- page title and bounded plain-text metadata for supported text content;
- source references.

Binary bodies are not parsed in AI4A. Raw page bodies are not copied into audit
events. Network errors map to stable error codes and safe messages.

## 12. Agent Prompt

The prompt must no longer claim that exactly three tools exist. It must state:

- use only the FocusProof tools exposed in the current Conversation;
- verify each evidence item with a matching available capability;
- never invent a tool or Observation;
- `unsupported` and `inconclusive` do not prove falsity;
- an artifact fact does not prove learner understanding;
- ask one focused question when facts are insufficient;
- submit a structured review draft when facts are sufficient;
- never include or imply the final numeric score in the draft.

## 13. Scoring Boundary

AI4A performs a targeted cleanup of general scoring:

- remove Web3 vocabulary such as nonce, gas, and transaction from generic
  concept detection;
- remove transaction-specific branches from the general scoring path;
- preserve the existing dimensions and API response shape;
- use evidence specificity, goal alignment, answer quality, output, and
  reflection as domain-general signals;
- keep any future Web3 interpretation behind a Web3 plugin boundary.

Tool observations may support findings and confidence. They cannot directly set
the final score or status.

## 14. Error Handling

Expected tool failures are represented as typed observations so the agent can
decide whether to ask for evidence or a learner explanation.

Examples:

- missing evidence: `failed / evidence_not_found`;
- mismatched type: `unsupported / evidence_type_unsupported`;
- blocked URL: `failed / url_blocked`;
- DNS or timeout failure: `inconclusive / network_unavailable`;
- oversized response: `failed / response_too_large`;
- unsupported content type: `unsupported / content_type_unsupported`.

Unexpected SDK or persistence failures still propagate to the runtime failure
boundary. They must not be disguised as successful observations.

## 15. Persistence And Event Semantics

- Native OpenHands ActionEvents and ObservationEvents remain the runtime facts.
- FocusProof audit events remain idempotent query projections identified by the
  source OpenHands event ID.
- Reconciliation must not duplicate projected verification results.
- Restart recovery must preserve Conversation ID and native event history.
- Changing capability metadata never mutates historical observations.
- Tool and verifier versions are included in new observations for auditability.

## 16. API And Frontend Compatibility

AI4A must preserve the current public API endpoints and response contracts.
The AI3 frontend must continue to submit `evidenceType`, `textContent`,
`sourceUrl`, and metadata without a required migration.

No frontend, contract, wallet, or chain environment changes belong to AI4A.

## 17. Security Constraints

- All AI4A tools are read-only and idempotent.
- Repository evidence cannot be supplied by the LLM as trusted tool input.
- Network access exists only in the URL capability.
- URL retrieval uses explicit SSRF controls and bounded resources.
- Tool errors never expose secrets or internal paths.
- OpenHands default programming and workspace tools remain disabled.
- Real LLM tests require an explicit marker and are excluded by default.

## 18. Test Strategy

Tests use OpenHands SDK native types and verify behavior at four levels.

### 18.1 Registry Unit Tests

- built-in registration;
- duplicate rejection;
- domain and evidence-type matching;
- disabled capability filtering;
- stable ordering;
- concurrent reads and lifecycle cleanup.

### 18.2 Verifier Unit Tests

- repository-reference-only actions;
- missing and mismatched evidence;
- short/generic and concrete text facts;
- URL normalization;
- IPv4 and IPv6 SSRF cases;
- DNS rebinding-resistant request checks;
- redirect revalidation;
- timeout, size, and content-type limits;
- safe error mapping;
- absence of score or verdict fields.

### 18.3 Runtime Integration Tests

- session-specific OpenHands tool assembly;
- default OpenHands tools remain absent;
- ActionEvent precedes its ObservationEvent;
- observations enter the native EventLog before review extraction;
- projection remains idempotent;
- Conversation restart preserves history and toolset diagnostics.

### 18.4 Product Regression Tests

- existing session and review APIs remain compatible;
- generic scoring no longer depends on Web3 vocabulary;
- weak evidence cannot receive a high score;
- a better follow-up answer may improve confidence;
- all non-real-LLM backend tests, Ruff, and Mypy pass.

## 19. Ownership And Allowed Changes

Before implementation, AI0 must update the task board to define AI4A as a
temporary backend-framework role. This prevents conflict with the existing AI4
contract/QA/deployment role, which becomes AI4B.

AI4A may modify:

- `agent-server/focusproof/openhands_runtime/`;
- the narrowly affected general scoring modules;
- `agent-server/tests/`;
- test fixtures;
- `docs/research/`;
- necessary Python dependency declarations.

AI4A must not modify:

- `frontend/`;
- `contracts/`;
- `.env` or credentials;
- `var/` runtime data;
- OpenHands SDK source code;
- public architecture, protocol, or task-board documents without AI0 approval.

## 20. Deliverables And Exit Criteria

Required deliverables:

1. Capability metadata and registry.
2. Deterministic per-session OpenHands tool assembly.
3. Text verification tool.
4. SSRF-safe URL verification tool.
5. Unified verification observation contract.
6. Updated agent prompt.
7. Domain-general scoring cleanup.
8. Unit, runtime integration, recovery, and regression tests.
9. `docs/research/AI4A_GENERAL_VERIFICATION_FRAMEWORK_REPORT.md`.

AI4A is complete only when:

- the official review endpoint still runs through OpenHands LocalConversation;
- native ActionEvent and ObservationEvent ordering is verified;
- no default OpenHands programming tool is exposed;
- text and URL evidence produce structured, sourced observations;
- URL SSRF and resource limits are tested;
- observations cannot assign final learning scores or verdicts;
- existing frontend-facing APIs remain compatible;
- default tests do not consume a real LLM key;
- the worktree is clean after intentional commits;
- implementation stops before code execution, multimodal processing, Web3 RPC,
  contracts, deployment, or AI4B work.
