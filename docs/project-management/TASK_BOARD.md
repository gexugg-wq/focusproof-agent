# FocusProof AI Task Board

Version: v0.5
Runtime direction: Python Agent Server with OpenHands Conversation as core runtime
Project root: `/home/holy/web3/focusproof-agent`

## 1. Global Rules

Every AI must read these files before working:

1. `docs/architecture/ARCHITECTURE.md`
2. `docs/architecture/OPENHANDS_REUSE_STRATEGY.md`
3. `docs/protocol/EVENTS.md`
4. Its own section in this task board.

Every AI must report:

- files created or changed,
- interfaces added or changed,
- commands run,
- test results,
- known limitations,
- whether public protocol files changed,
- whether it touched files outside its allowed area.

Only AI0 can approve public protocol changes.

## 2. Current Architecture Decision

FocusProof is a general learning verification product. Web3 is only the first domain plugin.

Current decision:

- Frontend uses Next.js and TypeScript.
- Agent runtime uses Python.
- Agent API uses FastAPI.
- Tool executors use Python.
- Contracts use Solidity.
- OpenHands SDK should be used directly, with OpenHands Conversation/ConversationState/EventLog acting as the official agent runtime path.

The project should not be split into too many worker AIs during the demo stage.
Five logical worker roles are enough; AI4 is split into sequential AI4A and AI4B
phases so backend framework work cannot be confused with contract deployment work:

- AI0: controller and architect.
- AI1: project scaffold and OpenHands reuse investigation.
- AI2: Python Agent Server, OpenHands SDK integration, Conversation-backed runtime, learning agent logic and tools.
- AI3: frontend, wallet UX and user flows after AI2 promotes Conversation into the official review path.
- AI4A: general verification tool framework on the existing OpenHands runtime.
- AI4B: contract, integration tests, security and deployment.

## 3. Directory Ownership

| AI | Role | Allowed write areas | Forbidden areas |
|---|---|---|---|
| AI0 | Controller / Architect | `docs/` | Unreviewed product implementation |
| AI1 | Scaffold + OpenHands Feasibility | root config, `agent-server/` scaffold, `frontend/` scaffold, `contracts/` scaffold, `docs/research/` | Product logic, scoring, Web3 RPC implementation |
| AI2 | Python Agent Server + OpenHands Conversation Runtime | `agent-server/`, `fixtures/`, dependency config, `docs/research/` runtime reports | Frontend UI ownership, Solidity contract ownership |
| AI3 | Frontend + Wallet UX | `frontend/` | LLM secrets, database direct writes, server-side scoring |
| AI4A | General Verification Framework | `agent-server/focusproof/openhands_runtime/`, narrowly affected `agent-server/focusproof/domain/` modules, `agent-server/tests/`, `fixtures/`, `docs/research/`, necessary Python dependency declarations | `frontend/`, `contracts/`, `.env`, `var/`, OpenHands SDK source, public protocol or architecture changes without AI0 approval |
| AI4B | Contract + QA + Deployment | `contracts/`, `scripts/`, `docs/security/`, `docs/deployment/`, cross-system tests | Product scoring rewrites without AI0 approval |

Two AIs must not edit the same file at the same time.

## 4. AI0: Controller / Architect

Responsibilities:

- maintain architecture documents,
- approve public protocol changes,
- split tasks,
- review cross-module boundaries,
- keep OpenHands reuse scoped,
- prevent Web3 assumptions from leaking into the general runtime,
- perform final end-to-end acceptance.

Acceptance:

- docs describe the same architecture,
- every AI has clear input, output and write boundary,
- public Event, Action and Observation changes are tracked,
- OpenHands reuse decisions are documented.

## 5. AI1: Scaffold + OpenHands Feasibility

Goal:

Create a runnable monorepo skeleton in `/home/holy/web3/focusproof-agent` and investigate how OpenHands SDK can be directly reused.

Must create or verify:

- `frontend/` skeleton,
- `agent-server/` Python package skeleton,
- `contracts/` skeleton,
- `fixtures/` skeleton,
- `scripts/` skeleton,
- root README,
- `.gitignore`,
- `.env.example`,
- Python dependency file,
- frontend dependency file if frontend scaffold is created,
- basic health checks.

Must investigate:

- local OpenHands SDK path,
- whether Agent, Conversation, Tool or message/event abstractions can be imported directly,
- dependency weight of direct import,
- whether direct import pulls in unnecessary software-engineering product behavior,
- whether minimal local adapters are safer.

Deliverables:

- project scaffold,
- `docs/research/OPENHANDS_SDK_FEASIBILITY.md`,
- one minimal Python health test,
- command summary showing environment and test status,
- recommendation for AI2, with AI0 final decision favoring direct OpenHands SDK integration through an adapter layer.

Forbidden:

- implementing real scoring,
- implementing Web3 RPC verification,
- implementing full runtime loop,
- implementing frontend product pages,
- deploying contracts.

## 6. AI2: Python Agent Server + OpenHands Conversation Runtime

Goal:

Build the backend brain of FocusProof: FastAPI API, direct OpenHands SDK integration, Conversation-backed learning review runtime, database/product projections, evidence tools and first Web3 plugin.

Must first implement OpenHands SDK integration:

- add OpenHands SDK as a local path dependency where possible,
- prove imports in a test,
- identify the concrete SDK Agent, Conversation, Tool, Action, Observation and Event classes or protocols used,
- create `agent-server/focusproof/openhands_adapter/`,
- wrap OpenHands Conversation, ConversationState, EventLog, Agent, Tool and event concepts behind FocusProof-specific adapter functions,
- keep FocusProof Evidence and ReviewResult as product-owned models.

Must implement runtime through the adapter:

- FocusProof session event ingestion as OpenHands-compatible message/event input,
- OpenHands-backed conversation creation for each learning session,
- ConversationState/EventLog as the runtime history owner,
- FocusProof view projection from the active Conversation event branch,
- OpenHands-style ActionEvent/ObservationEvent flow,
- tool execution boundary,
- safe run loop with max steps,
- recovery from persisted Conversation/EventLog state or a documented fallback,
- fake agent and fake tools for tests if real LLM config is unavailable.

The current AI2 state is not complete if real Conversation exists only at `/debug/openhands/conversation-test`. That path proves connectivity, but the official `/sessions/{session_id}/review` path must also be Conversation-backed before AI3 starts.

Must implement API:

- `POST /sessions`
- `POST /sessions/{session_id}/start`
- `POST /sessions/{session_id}/evidence`
- `POST /sessions/{session_id}/answer`
- `POST /sessions/{session_id}/review`
- `GET /sessions/{session_id}`
- `GET /sessions/{session_id}/events`
- `GET /profiles/{learner_id}/build-log`

Must implement learning review:

- weak evidence detection,
- goal/evidence mismatch detection,
- contradictory evidence detection,
- follow-up question generation,
- answer quality assessment,
- final score calculation,
- final review summary,
- next-step suggestion.

Must implement tools:

- text evidence parser,
- URL metadata/text reader,
- content hash tool,
- code snippet classifier,
- transaction hash format checker,
- transaction receipt reader,
- chain identity checker,
- wallet relation checker,
- contract address checker,
- block explorer URL parser.

Core constraints:

- OpenHands SDK should be imported directly unless a specific SDK class is impossible to adapt.
- Any local fallback must be documented as an adapter fallback, not as the primary runtime strategy.
- Runtime must not hardcode Web3.
- Web3 must live as a domain plugin.
- Tools return Observations only.
- Tool facts cannot directly become final learning judgment.
- Time cannot be enough proof.
- Successful transaction cannot equal understanding.

Required tests:

- OpenHands SDK import works in the project venv,
- FocusProof adapter can create or wrap an OpenHands conversation/runtime object,
- official `/sessions/{session_id}/review` can use a Conversation-backed runtime mode,
- submitted goal/evidence/answer are represented as runtime events/messages,
- OpenHands-generated or adapter-generated action events are projected into FocusProof events,
- tool observations are returned to the event ledger before scoring,
- event sequence ordering,
- replay rebuilds FocusProof view,
- agent returns one action through the adapter path,
- verify action creates tool request and observation event,
- interrupted session can recover,
- max step limit exits safely,
- weak generic evidence cannot get high score,
- valid transaction plus weak explanation remains low or needs more verification,
- better follow-up answer can improve review confidence.

## 7. AI3: Frontend + Wallet UX

Goal:

Build the user-facing web app for general learning verification, with Web3 evidence as the first specialized flow.

Start condition:

AI3 starts only after AI2 has promoted OpenHands Conversation from debug spike into the official `/sessions/{session_id}/review` orchestration path, or AI0 explicitly approves a temporary frontend prototype against deterministic backend APIs.

Must support:

- create learning goal,
- choose learning domain,
- start/end session,
- submit text evidence,
- submit URL evidence,
- submit Web3 evidence,
- answer agent follow-up questions,
- view review status and score,
- view findings and next step,
- view Build Log,
- connect wallet,
- request proof recording after review.

UX requirements:

- The first screen should be the actual learning verification app, not a marketing landing page.
- The app must make it clear that FocusProof judges evidence credibility, not human worth.
- General domain must work without wallet.
- Web3 domain may show wallet and transaction fields.
- Review loading, failure and retry states must exist.

Frontend must not:

- store LLM secrets,
- directly calculate final score,
- directly verify Web3 evidence,
- directly write database,
- write proof before backend review is complete.

## 8. AI4A: General Verification Tool Framework

Goal:

Extend the existing OpenHands-native review runtime with a FocusProof capability
registry, deterministic per-session tool assembly, and safe text and URL evidence
verification. AI4A must not replace Conversation, Agent.step, the native EventLog,
or the SDK Action/Observation/Tool protocol.

Required design and plan:

- `docs/superpowers/specs/2026-07-13-ai4a-general-verification-framework-design.md`
- `docs/superpowers/plans/2026-07-13-ai4a-general-verification-framework.md`

Must implement:

- FocusProof capability metadata over the OpenHands SDK tool registry,
- deterministic domain/evidence-type capability selection,
- per-session OpenHands Tool assembly,
- text verification by authoritative repository evidence ID,
- SSRF-safe bounded URL verification,
- a shared structured verification Observation envelope,
- prompt updates that describe available tools without a fixed tool count,
- targeted removal of Web3 assumptions from general scoring,
- runtime, recovery, API regression, security and type-checking tests.

Core constraints:

- Continue to use OpenHands `Agent`, `LocalConversation`, `ConversationState`,
  EventLog, `ToolDefinition`, `ToolExecutor`, Action and Observation directly.
- Agent actions carry evidence references, not authoritative evidence bodies.
- Tool executors load evidence through the trusted FocusProof repository.
- Tools return observed facts only and never assign final scores or learning verdicts.
- Native ActionEvents and ObservationEvents remain runtime facts.
- FocusProof audit events remain idempotent projections.
- OpenHands default programming and workspace tools remain disabled.
- Text and URL are the only new real verification capabilities in AI4A.
- Code execution, Web3 RPC, OCR, ASR, PDF, contracts and deployment are out of scope.
- Default tests must not consume a real LLM key.

Deliverable:

- `docs/research/AI4A_GENERAL_VERIFICATION_FRAMEWORK_REPORT.md`

AI4A stops after local commits and AI0 review. It must not push or begin AI4B.

## 9. AI4B: Contract + QA + Deployment

Goal:

Implement lightweight proof recording, then verify the whole system with integration, security and deployment checks.

Contract fields:

- `sessionId`,
- `learner`,
- `domain`,
- `score`,
- `effectiveMinutes`,
- `summaryHash`,
- `proofVersion`,
- `createdAt`.

Contract must support:

- authorized proof recorder,
- duplicate session rejection,
- score bounds,
- `ProofRecorded` event,
- read proof by session.

Contract must not store:

- full notes,
- images,
- audio,
- code,
- full conversations,
- raw private evidence.

Integration tests:

- only generic summary submitted,
- notes plus code,
- notes plus error log,
- valid transaction but weak explanation,
- invalid transaction,
- wrong chain transaction,
- wallet mismatch,
- evidence-goal mismatch,
- contradictory evidence,
- score improves after good follow-up answer,
- proof recording blocked before review completion.

Security checks:

- prompt injection,
- XSS,
- SSRF,
- oversized files,
- unauthorized session read,
- forged wallet address,
- replay request,
- unreviewed proof recording,
- LLM output forging tool observations.

Deployment docs:

- local WSL development,
- frontend dev server,
- agent-server dev server,
- environment variables,
- database setup,
- Monad Testnet deployment,
- production deployment notes.

## 10. Development Phases

| Phase | Content | Owner | Status |
|---|---|---|---|
| 0 | v0.5 architecture and task-control docs | AI0 | in progress |
| 1 | scaffold and OpenHands SDK feasibility | AI1 | done |
| 2 | direct OpenHands SDK import and adapter spike | AI2 | done |
| 3 | OpenHands Conversation core integration and persistence hardening | AI2 | done |
| 4 | frontend MVP and general-learning acceptance correction | AI3 | done |
| 5 | general verification tool framework: registry, text and URL | AI4A | next |
| 6 | contract, integration, security and deployment | AI4B | pending |
| 7 | multimodal expansion | AI2 + AI3 + AI4B | later |

## 11. First Execution Task

The next executable task is AI4A.

AI4A must extend the official OpenHands Conversation-backed review path rather
than build a parallel runtime. It follows the approved design and implementation
plan, works on a dedicated `ai4a-general-verification-framework` branch, uses
TDD, commits locally in reviewable increments, writes the required research
report, and stops for AI0 acceptance without pushing.
