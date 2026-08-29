# FocusProof Agent Architecture

Version: Architecture Baseline v0.9 (2026-08-28 knowledge sync)
Primary runtime: Python Agent Server
Frontend: Next.js and TypeScript
Optional domain plugin backlog: domain-specific verification, deferred
Long-term scope: general knowledge learning verification

## 1. Product Goal

FocusProof Agent verifies whether a learning session produced credible, reviewable learning evidence. It does not claim to read the learner's mind and does not simply reward time spent. It evaluates the relationship between:

- the learner's stated goal,
- submitted evidence,
- tool observations,
- explanation quality,
- follow-up answers,
- learning output,
- reflection and next-step plan.

The v0.9 runtime has one official OpenHands SDK 1.31.0 Conversation/agent loop,
one capability registry/tool assembler projection, and one production manager.
`openhands_adapter/real_conversation.py` is authorized gate/debug code and is
unreachable from the production application import graph. Readiness validates
the SDK public extension-point contract without provider calls. Media ingestion
uses bounded upstream spooling and one Clamd absolute deadline. SQL bind values
are hidden in application and migration engines; operational logs contain only
identifiers, states, and allowlisted error codes.
The accepted product is the domain-general text/URL learning flow. Web3 was
considered in an early design, but is not a current architecture dependency,
runtime capability, or AI4C production-readiness deliverable.

Current authoritative status (2026-08-27): AI4C engineering and AI5
image-foundation/runtime acceptance are complete. The AI5.8 independent audit
was initially rejected, then accepted after three fix rounds and an independent
Round 3 re-verification. Media remains default-off and detachable. The former chain-specific plugin has been removed from active runtime, UI, configuration, dependencies, and contract sources.
An explicitly enabled local acceptance completed one text-plus-PNG learning
session through the official OpenHands runtime and `openai/qwen3.7-plus`, then
persisted the review and seven-event Build Log. Real visual-provider use is
still default-off; public deployment, managed OIDC,
and external long-term operations/SLOs are not authorized. Audio/PDF/OCR/ASR
are not implemented. AI6 multimodal expansion requires separate AI0 approval.

### 1.1 AI4C Production-Readiness Boundary

AI4C keeps the accepted domain-general text/URL product and executes four
strictly sequential implementation gates after its design is accepted:

1. real-LLM operations through the OpenHands SDK LLM/LiteLLM integration;
2. FastAPI OIDC identity verification and product authorization;
3. reproducible single-host OCI staging with PostgreSQL and paired OpenHands
   persistence recovery;
4. final production-readiness acceptance.

DashScope is the first real-provider acceptance instance, not an architecture
dependency. The FastAPI OIDC verifier is the only authoritative application
identity boundary. AI4C does not add Web3, contracts, wallets, on-chain proof,
multimodal evidence or a second OpenHands runtime.

AI4C is not a public-deployment authorization. If only a local OIDC test issuer
or isolated staging substitutes are exercised, the strongest result is
`staging-ready with blockers`.

Staging and production require the explicit deployment setting
`LITELLM_LOCAL_MODEL_COST_MAP=true`. A standard-library preflight validates the
exact value before importing OpenHands or LiteLLM, so missing, false, whitespace,
or malicious values fail closed before any pricing-map network path. Local-dev
and deterministic-test set the bundled-map invariant only when entering the
OpenHands package boundary; ordinary FocusProof imports do not mutate it.

The official OpenHands SDK `Conversation` and native EventLog are the sole
runtime loop and runtime fact source. FocusProof does not implement a fallback
Agent loop, Conversation or EventLog. Its in-memory and persistent
`AuditProjectionStore` implementations retain only product-approved query
projections and cannot schedule Agent steps, execute tools or replace native
OpenHands restoration.

Media admission security remains an application-owned boundary outside Agent
decisions and generic scoring. Generic scoring does not branch by
modality, and audio/PDF/OCR/ASR remain backlog/deferred pending AI0.

The product database owns sessions, evidence metadata, authorization,
reviews, build logs, and a read/query audit projection. It does not restore or
drive the Agent runtime. Native OpenHands persistence and EventLog own runtime
events, ordering, replay, and Conversation restoration.

## 2. Historical/Superseded v0.1 Design Archive

Everything in this section through the end of the document is retained only
as historical planning context. References to TypeScript runtime mirrors,
wallet UX, Monad, contracts, on-chain proof, transaction verifiers, or a
FocusProof-owned EventLog are superseded by sections 1 and 1.1 above and must
not be implemented as current architecture.

### 2.1 What Changed in the Historical v0.1 Plan

The previous plan used a TypeScript-first runtime. The v0.2 plan changes the runtime to Python so the project can directly use OpenHands SDK agent-runtime abstractions.

Changed:

- Runtime moves from `app/src/runtime` to `agent-server/focusproof/runtime`.
- Backend agent boundary becomes Python FastAPI.
- Tool executors are Python-first.
- OpenHands SDK becomes a concrete feasibility target, not only an inspiration.
- Project layout becomes a monorepo with `frontend`, `agent-server`, `contracts`, `docs`, `fixtures` and `scripts`.

Unchanged:

- EventLog remains the source of truth.
- Conversation remains the run container.
- Agent still performs one decision step at a time.
- Action and Observation stay separated.
- Full notes, raw files and private evidence do not go on-chain.
- The smart contract stores only lightweight proof results.

## 3. Target System Architecture

```text
Frontend: Next.js / TypeScript
  |
  | goal input, session timer, evidence upload, wallet UX, review display
  v
Agent API: Python FastAPI
  |
  | auth, session API, event persistence, agent run orchestration
  v
FocusProof Runtime: Python
  |
  | EventLog, Conversation, ViewBuilder, Agent.step, Action, Observation
  v
Domain Plugins
  |
  | general learning, Web3 learning, programming, math, language, reading
  v
Verification Tools
  |
  | URL reader, text parser, tx verifier, contract verifier, OCR, ASR, PDF parser
  v
Database
  |
  | sessions, evidence metadata, events, observations, reviews, build logs
  v
Smart Contract on Monad Testnet
  |
  | sessionId, learner, domain, score, effectiveMinutes, summaryHash
  v
User Profile / Build Log
```

## 4. OpenHands Runtime Position

We do not fork the whole OpenHands product. We directly use the OpenHands SDK runtime mechanics that keep agent work auditable and recoverable.

Must use directly or adapt tightly:

- `Conversation(...)` as the local/remote factory boundary.
- `LocalConversation` as the local learning-review run container.
- `ConversationState` as the owner of execution status, active branch, EventLog and View.
- OpenHands `EventLog` semantics as the runtime fact ledger.
- `Agent.step()` as the one-step decision boundary.
- `ActionEvent` before tool execution.
- `ToolDefinition` / `ToolExecutor` for executable capabilities.
- `ObservationEvent` after tool execution.
- Agent Server / EventService / WebSocket patterns as the future remote event boundary.

Use as reference only:

- Software-engineering loop structure.
- Workspace management.
- Browser or terminal automation patterns.
- Task tracking patterns.

Do not use in the first demo:

- TerminalTool as a default learning verifier.
- FileEditorTool as a default learning verifier.
- Full OpenHands RemoteConversation if it pulls in too much product behavior.
- OpenHands task-completion logic as learning score.

FocusProof owns the learning-specific protocol:

- Evidence schema.
- Domain plugin interface.
- Scoring dimensions.
- Review statuses.
- Build Log generation.
- On-chain proof payload.

Direct reuse is a project-level constraint, not an implementation preference. If the pinned OpenHands SDK exposes a public capability for Agent, Conversation, EventLog/View, Message/Action/Observation events, tools, cancellation, recovery or callbacks, the implementation must use that capability. FocusProof adapters may add product semantics and projections, but must not recreate an equivalent runtime mechanism. Any exception requires the SDK gap record defined in `docs/architecture/OPENHANDS_REUSE_STRATEGY.md`.

See `docs/architecture/OPENHANDS_REUSE_STRATEGY.md`.

Important correction:

```text
A real OpenHands LLM Conversation that only exists behind a debug endpoint is not enough.
The official learning review path must be promoted to a Conversation-backed runtime.
```

## 5. General Knowledge Model

FocusProof is domain-general. A learning domain is a plugin, not a hardcoded product branch.

Core runtime must not assume Web3. It only understands:

- learning goals,
- evidence,
- questions and answers,
- observations,
- scoring requests,
- review summaries.

Each domain plugin defines:

- accepted evidence types,
- domain-specific checks,
- follow-up question templates,
- evidence normalization rules,
- scoring hints,
- Build Log formatting hints.

Initial plugins:

- `GeneralLearningPlugin`: text notes, URLs, summaries, concept explanations.
- `Web3LearningPlugin`: transaction hash, contract address, wallet address, block explorer URL, Solidity snippets.

Future plugins:

- `ProgrammingLearningPlugin`: code, tests, commits, error logs, terminal output.
- `MathLearningPlugin`: derivations, solved problems, proof steps.
- `LanguageLearningPlugin`: vocabulary, speaking transcript, writing sample.
- `ReadingLearningPlugin`: highlights, notes, structured recall.
- `ResearchLearningPlugin`: paper notes, citation graph, claim extraction.

## 6. Runtime Responsibilities

The Python runtime is responsible for:

- creating or restoring an OpenHands Conversation for each learning session,
- converting user goal/evidence/answer submissions into message or event entries,
- letting ConversationState/EventLog own the runtime history,
- deriving FocusProof AgentView from the active OpenHands/FocusProof event branch,
- calling `Agent.step()` through the Conversation run loop,
- converting OpenHands ActionEvents into allowed FocusProof tool requests,
- writing tool Observations back as ObservationEvents and FocusProof audit events,
- enforcing max steps, timeouts and recovery behavior,
- preventing tool results from being treated as user understanding.

The runtime must not contain:

- final domain scoring details,
- Web3 RPC logic,
- frontend state,
- database schema decisions beyond repository interfaces,
- smart contract write logic.

## 7. Agent Responsibilities

The learning review agent is responsible for choosing the next action:

- ask a follow-up question,
- request stronger evidence,
- verify submitted evidence,
- calculate a score,
- generate a summary,
- finish review.

The agent must not directly execute tools. It returns or emits an action request. Tool executors return Observations. Runtime writes both into the event ledger so the next Agent step sees the updated View.

## 8. Conversation Semantics

FocusProof must treat a learning session as a Conversation-backed run, not as a plain API record.

OpenHands roles map to FocusProof as follows:

| OpenHands concept | FocusProof meaning |
|---|---|
| `Conversation` factory | Creates the local or remote learning-review runtime |
| `LocalConversation` | Runs the review loop in the Python Agent Server |
| `ConversationState` | Owns execution status, active event branch, View and recovery |
| `EventLog` | Runtime fact ledger |
| `state.view` | Current context given to the Agent/LLM |
| `MessageEvent` | User goal, evidence or answer entering the runtime |
| `ActionEvent` | Agent's proposed next work |
| `ToolDefinition` / `ToolExecutor` | Safe evidence verification capability |
| `ObservationEvent` | Tool result returned to the ledger |
| Agent Server | Remote boundary for creating sessions, sending messages and streaming events |

The FocusProof database and API may keep product projections, but they must not become a parallel substitute for ConversationState.

## 9. Evidence And Review Principles

Evidence strength depends on specificity, alignment and explainability.

Examples:

- A transaction hash proves an on-chain interaction happened, but not that the learner understood it.
- A screenshot may prove the learner saw a tool, but not that they can explain it.
- A note summary may show recall, but weak evidence if it is generic.
- A wrong error log plus a clear correction can be strong evidence of real learning.

Review status values:

- `VerifiedLearning`
- `LikelyLearning`
- `WeakEvidence`
- `NeedsMoreVerification`
- `InsufficientEvidence`
- `ContradictoryEvidence`

Default scoring dimensions:

- goal clarity,
- evidence specificity,
- goal alignment,
- understanding,
- output,
- reflection.

## 10. Project Layout

```text
/home/holy/web3/focusproof-agent/
  docs/
    README.md
    architecture/
      ARCHITECTURE.md
      OPENHANDS_REUSE_STRATEGY.md
    protocol/
      EVENTS.md
    project-management/
      TASK_BOARD.md
    security/
    deployment/
    superpowers/
      plans/
  frontend/
    src/
    tests/
  agent-server/
    focusproof/
      api/
      bootstrap/
      config/
      contracts/
      database/
      domain/
      media_adapters/
      media_core/
      media_projection/
      openhands_adapter/
      openhands_runtime/
      persistence/
      runtime/
      agents/
    tests/
      fixtures/
  contracts/
    README.md
  deploy/
  docs/
  frontend/
  scripts/
```

## 11. Suggested Tech Stack

Frontend:

- Next.js
- TypeScript
- Tailwind CSS
- wagmi and viem for wallet UX

Agent Server:

- Python 3.12
- FastAPI
- Pydantic
- pytest
- httpx
- SQLAlchemy or SQLModel
- SQLite for demo
- PostgreSQL-compatible design for production

Agent Runtime:

- OpenHands SDK direct import.
- FocusProof adapters around OpenHands Conversation, Event, Tool and Agent objects.
- FocusProof-owned runtime additions only for a documented SDK gap, with tests and a removal plan.

Tools:

- web3.py or JSON-RPC client for Monad verification.
- BeautifulSoup/readability-style extraction for URL text.
- OCR, ASR and PDF parsing added after text/Web3 MVP is stable.

Contracts:

- Solidity
- Foundry preferred in WSL/Linux
- Optional proof recording is designed in AI4B.0, verified locally before any Monad Testnet deployment.

## 12. Security Boundaries

- LLM secrets stay only in the Python Agent Server.
- Browser never directly calls LLM APIs.
- User input cannot overwrite tool observations.
- Tool observations cannot directly become final score.
- Raw notes, images, audio, code and conversations are not stored on-chain.
- Contract writes happen only after a completed review.
- Tool calls must have timeout, size and network limits.
- Web3 verification must check chain identity and wallet relation when relevant.
- Logs must not store private keys, signatures or unnecessary raw private content.

## 13. Demo Success Criteria

The first usable demo should let a user:

1. Create a learning goal in any domain.
2. Run or manually record a learning session.
3. Submit text evidence, URLs and Web3 evidence.
4. Receive follow-up questions from the agent.
5. Receive a score, status, findings, summary and next step.
6. Save a Build Log.
7. Optionally record a lightweight proof result to Monad Testnet.

The demo is successful only if weak evidence cannot easily receive a high score without specific evidence or explanation.
