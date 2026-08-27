# FocusProof AI Task Board

Version: v0.9
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
- the pinned OpenHands SDK version and dependency source when runtime behavior is involved,
- OpenHands public APIs inspected and directly reused,
- every FocusProof-owned adapter or SDK gap, with justification and removal plan.

Only AI0 can approve public protocol changes.

### OpenHands Direct-Reuse Gate

All implementation AIs must treat `docs/architecture/OPENHANDS_REUSE_STRATEGY.md` section 2.1 as a mandatory acceptance gate.

- Search the pinned SDK public API, source and tests before designing runtime infrastructure.
- Directly use suitable OpenHands public capabilities.
- Keep FocusProof code at the product boundary: learning semantics, policy, authorization, persistence projections and API translation.
- Do not create an OpenHands-inspired mirror, compatibility runtime or second protocol when the SDK already provides the behavior.
- Do not copy OpenHands source into this repository, patch SDK internals or mutate private SDK state.
- Record a formal SDK gap before implementing missing runtime behavior locally.

AI0 must reject duplicated runtime semantics even when tests pass. Each phase acceptance report must contain two explicit sections: `OpenHands APIs Reused` and `FocusProof-Owned SDK Gaps`. An empty gap section is valid; an undocumented gap is not.

## 2. Current Architecture Decision

FocusProof is a general knowledge learning verification product. Web3 is a
deferred optional-plugin concept, not the first required plugin and not part of
the default runtime.

Current decision:

- Frontend uses Next.js and TypeScript.
- Agent runtime uses Python.
- Agent API uses FastAPI.
- Tool executors use Python.
- Domain-specific plugins require separate approval and explicit enablement.
- OpenHands SDK should be used directly, with OpenHands Conversation/ConversationState/EventLog acting as the official agent runtime path.

AI4 engineering completion means the accepted deterministic technical scope is
implemented and green. It is distinct from external release authorization.
Real-provider, managed OIDC, retained SDK-equivalence artifacts, external
staging, and public deployment remain blocked until their explicit gates are
authorized and evidenced.

The project should not be split into too many worker AIs during the demo stage.
Five logical worker roles are enough; AI4 is split into sequential AI4A, AI4B
and AI4C phases so verification-framework, release-baseline and production-readiness
work cannot be confused:

- AI0: controller and architect.
- AI1: project scaffold and OpenHands reuse investigation.
- AI2: Python Agent Server, OpenHands SDK integration, Conversation-backed runtime, learning agent logic and tools.
- AI3: frontend and general learning user flows after AI2 promotes Conversation into the official review path.
- AI4A: general verification tool framework on the existing OpenHands runtime.
- AI4B: general integration tests, security and release-readiness baseline.
- AI4C: production identity, real-LLM operations and reproducible staging deployment.
- AI5: multimodal image foundation, owned as a conditional media capability on the existing OpenHands runtime.

### AI5 final status (2026-08-25 authoritative sync)

This section supersedes older AI5 completion wording elsewhere in this board,
including AI5.7 pending, external-Clamd-blocked, and whole-AI5-incomplete
statements. Historical sections remain for provenance.

1. The deterministic image foundation and the OpenHands SDK 1.31.0 native
   `ImageContent` -> `MessageEvent` -> `Conversation` event chain are complete.
2. The pinned real-PNG acceptance is complete. Its immutable input is
   `docs/research/assets/ai5/task7/chromium-success.png`, SHA-256
   `7ee186d8b0efa5ca62039ab97655e811e748c86696fee1752f8c0fc7ef3f468e`.
   V6 used `openai/qwen3.7-plus`: exactly one visual provider completion,
   zero agent-decision completions, and no retry. It produced eight structured
   visual facts with `parseStage=complete`, `errorCategory=none`, review
   `completed`, and runner `PASS`. Independent review concluded
   `V6_REAL_IMAGE_GATE_FINAL_ACCEPTED`. The V6 report SHA-256 is
   `80305ffa837cf42bb79ab3a10f2e14c7ffd83ff426ed95fab01d1037f750afc3`;
   its sidecar SHA-256 is
   `80d76c711bb3c168cb0bbc2b992c1734e6201a69e527770bb0f473fca079ae17`.
3. AI5.7 is complete. Two live Clamd five-case matrices passed; fresh final
   acceptance again passed clean, EICAR/malicious, timeout, unavailable, and
   daemon-error. Oversize remains proven at the adapter boundary. Raw media
   cannot reach LLM/OpenHands events before a clean receipt.
4. AI5.8 independent audit: initial `REJECTED`; Fix Rounds 1/2/3 completed;
   final Round 3 independent re-verification `ACCEPTED`.
5. PostgreSQL revision `0006_media_scan_receipts` passed 0005 -> head ->
   repeated head -> downgrade -> head. Image final publish and Review reuse one
   `FileSessionRunLock`; expensive read/scan/validation/normalization/staging is
   outside it, stage -> publish is cancellation gated, and
   finalize/reference/confirm is inside it.
6. The permanent RED oracle rejects Review-lock bypass. The normal
   publish/review barrier and restart-reconstructed barrier are green.
7. Round 2 default gate: `1900 passed, 1 skipped, 19 deselected`. Round 3
   focused evidence: `85/94 passed`; targeted/production strict mypy, Ruff,
   diff, and cached-empty gates passed.
8. FocusProof remains general-purpose. The retired Monad plugin is no longer an
   active runtime capability; active plugin count=0. Audio/PDF/OCR/ASR are not
   implemented. Public deployment, managed OIDC, and
   external long-term operations/SLOs remain unauthorized.

AI5 quality-gate closure is represented by `scripts/run_quality_gate.py`.
`--tier fast` is the default deterministic no-Docker/no-network/no-real-LLM
gate. `--tier integration` inherits fast and adds PostgreSQL, recovery,
deterministic E2E, and Clamd integration without real LLM calls. `--tier
release` inherits integration but requires explicit `--allow-real-provider`
before live Clamd, real Qwen/OpenHands, and final manifest steps can execute;
without that flag it exits closed. Expected cost is none for fast, local
infrastructure time for integration, and external provider/daemon cost for
release.

Next phase: **AI6 multimodal expansion requires separate AI0 approval**.
AI6 voice/audio work has not started.
Do not announce or begin an audio phase without that approval. Directly reuse
official OpenHands SDK capabilities; never build imitation Runtime,
Conversation, EventLog, Message/Action/Observation, or Tool abstractions.

## 3. Directory Ownership

| AI | Role | Allowed write areas | Forbidden areas |
|---|---|---|---|
| AI0 | Controller / Architect | `docs/` | Unreviewed product implementation |
| AI1 | Scaffold + OpenHands Feasibility | root config, `agent-server/` scaffold, `frontend/` scaffold, `contracts/` scaffold, `docs/research/` | Product logic, scoring, Web3 RPC implementation |
| AI2 | Python Agent Server + OpenHands Conversation Runtime | `agent-server/`, `fixtures/`, dependency config, `docs/research/` runtime reports | Frontend UI ownership, Solidity contract ownership |
| AI3 | Frontend + Wallet UX | `frontend/` | LLM secrets, database direct writes, server-side scoring |
| AI4A | General Verification Framework | `agent-server/focusproof/openhands_runtime/`, narrowly affected `agent-server/focusproof/domain/` modules, `agent-server/tests/`, `fixtures/`, `docs/research/`, necessary Python dependency declarations | `frontend/`, `contracts/`, `.env`, `var/`, OpenHands SDK source, public protocol or architecture changes without AI0 approval |
| AI4B | General QA + Security + Release Readiness | cross-system tests, narrowly affected backend/frontend fixes, `docs/security/`, `docs/deployment/`, `docs/research/` | New product features, multimodal work, public deployment |
| AI4C | Production Readiness | identity/runtime/deployment modules approved by AI4C.0, their tests, deployment config and reports | Multimodal work, Web3 specialization, OpenHands mirror implementations, scoring rewrites without AI0 approval |
| AI5 | Multimodal Image Foundation | image media modules under `agent-server/focusproof/media_*`, conditional API/bootstrap/runtime contribution files, scoped media tests, frontend image evidence flow, and AI5 docs/reports | OpenHands SDK source, `.env` or secrets, `var/`, unapproved public protocol changes, second Runtime/Conversation/EventLog/Tool protocol, retired Monad/Web3 history |

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

Status: done.

Historical AI2 goal (superseded by the accepted status above):

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

Status: done for the accepted Conversation runtime, persistence, API and review
baseline. The original Web3 RPC verifier backlog was not part of final AI2
acceptance and remains deferred; Web3 is not assumed by the general runtime.

Goal:

Build the backend brain of FocusProof: FastAPI API, direct OpenHands SDK integration, Conversation-backed learning review runtime, database/product projections, and general evidence tools. The former first-Web3-plugin wording is superseded and was not accepted.

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

Accepted general tools and deferred domain backlog:

AI4A subsequently delivered real text and URL verification. Transaction receipt,
chain identity, wallet relation, contract-address and block-explorer verification
remain future Web3 plugin work; their presence below does not mark them complete.

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

Status: done for the frontend MVP and general-learning acceptance correction.

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

Status: completed and accepted at AI4A.3.1. Accepted commits end at `4387333`.

## 9. AI4B: General QA + Security + Release Readiness

Status: completed and accepted at `bf5c9a8`.

Delivered:

- cross-system backend, frontend and real-BFF browser acceptance,
- security boundaries for identity ownership, XSS, SSRF and bounded input,
- restart, recovery, locking and shutdown reliability,
- four-viewport production visual acceptance,
- local, staging and operations runbooks,
- final acceptance matrix and release-readiness report.

AI4B did not deploy publicly, run a Web3 transaction, record an on-chain proof,
add multimodal input or approve a production identity provider. Those remain
explicitly outside the accepted AI4B baseline.

## 10. AI4C: Production Readiness

Status: completed. AI4C.0-4 engineering implementation, deterministic local acceptance, and the 2026-08-12 formal real-provider General Core Gate are accepted. The gate passed both official text and URL scenarios through FastAPI, OpenHands SDK Conversation, Agent.step, and native Action/Observation/EventLog with active plugin count=0. This closes AI4C; it does not authorize public production release.

The highest honest release classification remains staging-ready with blockers. External release gates remain SDK-EQUIVALENCE blocked, CLEAN-STACK blocked, and external OIDC/staging blocked. Real-provider General Core Gate execution is complete and is no longer an outstanding blocker.

Goal:

Turn the accepted single-user/local MVP into a reproducible staging-ready
service without changing the domain-general learning-verification boundary.

AI4C is split into sequential gates:

- AI4C.0: architecture/specification, OpenHands reuse audit, threat model,
  provider decisions, migration boundaries and acceptance matrix.
- AI4C.1: real-LLM provider runtime, schema enforcement, bounded retries,
  deadlines, rate/cost controls, redaction and deterministic test separation.
- AI4C.2: verified identity, authorization, resource ownership, revocation and
  audit attribution, while retaining an explicitly isolated local-dev identity.
- AI4C.3: reproducible SDK/dependency packaging, PostgreSQL/staging validation,
  secrets/configuration, observability, backup/restore and deployment smoke.
- AI4C.4: end-to-end production-readiness acceptance and AI0 closure report.

Constraints:

- Reuse suitable public OpenHands SDK APIs directly; do not create equivalent
  FocusProof-owned Agent, Conversation, EventLog, Action, Observation or Tool
  runtime semantics.
- Every local runtime addition requires a documented SDK gap and removal plan.
- AI4C must not add OCR, ASR, image, audio or PDF input; those belong to AI5.
- AI4C must not make Web3, wallets, Monad or contracts part of the core path.
- No public deployment, merge or push is authorized by the design gate.
- Production code starts only after AI0 accepts the AI4C.0 written design and
  implementation plan.

## 11. AI5: Multimodal Input Foundation

Status: engineering accepted for the AI5 image foundation on 2026-08-14. Independent strict review returned `VERDICT: APPROVED` with no blocking, important or minor findings.

AI5 defines the image evidence foundation while continuing to reuse OpenHands directly. It does not create or imitate a second Agent runtime, Conversation loop, EventLog, Action/Observation model, or tool protocol.

Accepted AI5 scope:

- Task 1 completed: architecture, SDK and dependency contracts for OpenHands SDK 1.31.0.
- Task 2 completed: lifecycle ports, two-stage leases, migration and existing synchronous UoW integration.
- Task 3 completed: codec/store adapters and optional media build contract.
- Task 4 completed: streaming limits, multipart route and product capability disclosure.
- Task 5 completed for local/staging real-provider validation through official OpenHands SDK 1.31.0 `LLM`, `ImageContent`, `LocalConversation`, Action/Observation events and Tool surfaces. Visual support is explicit and disabled by default.
- Task 6 completed: conditional runtime contribution, scoped safe media facts and modality-neutral narratives.
- Task 7 completed: transparent BFF and server-driven image UI.
- Task 8 image-foundation engineering evidence completed: backup/restore, report, diff gate, deterministic default tests and a guarded real visual-provider test. Production scanning implementation and acceptance are incomplete; only `fake-clean` isolation is verified with `productionMalwareScanningVerified=false`. AI5.7 owns `ScannerPort`, `ScanResult`, the replaceable production adapter, and the fail-closed production boundary.

Final AI5 evidence:

- focused architecture/product/SDK: `43 passed, 1 skipped`
- media API/core/adapters/message content: `171 passed`
- runtime contribution/tool/scoring: `21 passed`
- default API/general core/retired-plugin historical gate: `76 passed`
- persistence/migrations/restart recovery: `114 passed`
- Alembic upgrade `0005` -> downgrade `0004` -> upgrade head: PASS
- explicit visual-provider capability tests: `2 passed`; complete LLM operations: `20 passed`
- real image provider gate: `PASS_LOCAL_STAGING_REAL_PROVIDER`
- guarded real-image test: `1 passed in 36.00s`
- `git diff --check` and Ruff: PASS

Disabled fresh imports are clean in all six process-isolated checks: each of
`focusproof.api.app`, `focusproof.openhands_runtime.manager`, and
`focusproof.openhands_runtime.synchronizer`, with `FOCUSPROOF_MEDIA_ENABLED`
either unset or `false`, reported `leaked=[]` for the forbidden media module
families.

OpenHands is directly reused through official `Message`, `ImageContent`, `Conversation`, and `ToolDefinition` surfaces. AI5 added no second Runtime, Conversation, EventLog, or Tool protocol.

Historical acceptance evidence retained for context:

- backend: `1238 passed, 1 skipped, 19 deselected`
- PostgreSQL: `5 passed`
- Docker core/media build: PASS
- frontend lint/typecheck/`114` Vitest/build: PASS
- default E2E: `28/28`
- retired-plugin E2E historical evidence: `4/4`
- staging restore: `1 passed`

## 12. Development Phases

| Phase | Content | Owner | Status |
|---|---|---|---|
| 0 | architecture and task-control baseline | AI0 | done |

| 1 | scaffold and OpenHands SDK feasibility | AI1 | done |
| 2 | direct OpenHands SDK import and adapter spike | AI2 | done |
| 3 | OpenHands Conversation core integration and persistence hardening | AI2 | done |
| 4 | frontend MVP and general-learning acceptance correction | AI3 | done |
| 5 | general verification tool framework: registry, text and URL | AI4A | done |
| 6 | general integration, security and release-readiness baseline | AI4B | done |
| 7 | production identity, real-LLM operations and reproducible staging | AI4C | done / AI0 accepted (engineering phase complete; not public production release) |
| 8 | AI5 image evidence foundation | AI5 | layered: image foundation + real vision accepted; production scanning implementation and acceptance incomplete; fake-clean isolation only |
| P1 | optional Web3 specialization and on-chain proof | domain plugin owners | backlog |

## 13. Next Execution Task

AI4C.0-4 engineering implementation and deterministic local acceptance are
complete, and AI0 has issued final acceptance. This is engineering-phase
closure only: public production release remains unauthorized while
SDK-EQUIVALENCE, CLEAN-STACK, and external OIDC/staging remain blocked. The
formal real-provider General Core Gate passed on 2026-08-12; it is no longer an
outstanding blocker. The maximum current classification is staging-ready with
public-release blockers.

AI5 image foundation engineering and the local/staging real visual-provider gate
are accepted. The real gate used a real PNG, official OpenHands LLM, and
`openai/qwen3.7-plus`; it completed two follow-up rounds and produced concrete
image facts. The corrected nested `reviewResult.score/summary` consumer and
fail-closed review state machine are independently `APPROVED_CONTRACT_GATE`
with 19 tests. The Review API, persistence, and OpenHands path had no defect.

Historical code-gate claim (superseded/non-production): deterministic scanner tests passed, but they established no production implementation or acceptance. Current evidence verifies only `fake-clean` isolation with `productionMalwareScanningVerified=false`; AI5.7 owns `ScannerPort`, `ScanResult`, the replaceable production adapter, and the fail-closed production boundary.
The separate external clean/EICAR run is `BLOCKED_EXTERNAL_SERVICE_GATE` /
`REAL_CLAMD_GATE_BLOCKED`: the pinned
`clamav/clamav:1.5.3-debian@sha256:e6243e...828c` pull timed out to
`registry-1.docker.io:443`; no probes ran and no container/image remains.
Network recovery or a reachable clamd endpoint is required. AI5 is therefore
not fully complete and public production upload remains unauthorized.


## 14. General Core Gate Closure Addendum

The former Monad plugin has been retired from active runtime, UI, scripts, and contract sources. The commit chain below remains as historical context for how the capability was first hidden before full removal.

Accepted implementation commit chain:

- `2950e14`: restored structured runtime-unavailable responses.
- `8662a9d`: hid wallet UI when Monad is disabled.
- `f57c8b5`: corrected the DashScope/OpenAI-compatible model format to `openai/qwen-plus`.
- `bbd7fc9`: preserved stable API errors while logging a redacted root cause for server-side diagnostics.
- `843531f`: made direct completion report-safe and enforced cross-scenario dynamic-question evidence.
- `184725a`: supplied valid explanatory text with the URL evidence scenario.
- `1c5032a`: recorded the formal General Core Gate acceptance report.
- `0b85b6f`: advanced the task-board roadmap to AI5.

Deterministic local evidence for the closure:

- isolated Alembic DB PASS
- backend `30 passed`
- frontend `5 passed`
- lint / typecheck / Ruff / Mypy / diff-check PASS
- independent review APPROVED

Formal real-provider product acceptance passed on 2026-08-12 with
`openai/qwen3.7-plus` through the official two-scenario text and URL product
path. The redacted results and native Action/Observation/Build Log evidence are
recorded in `docs/research/GENERAL_CORE_GATE_REPORT.md`.

AI5 image foundation engineering is accepted. Real visual-provider acceptance,
public-production malicious-file virus scanning, and future audio/PDF expansion
remain separate gates.

OpenHands is reused directly through the official Conversation, `Agent.step`,
native Action/Observation, and EventLog path. No mirror loop, second EventLog,
or alternate runtime or tool protocol is introduced.

### AI5.3 production media security

- Historical code-gate claim (superseded/non-production): 288 focused and 341 expanded tests passed with Ruff/Mypy clean.
- This did not implement or accept production scanning; only `fake-clean` isolation is verified with `productionMalwareScanningVerified=false`, and AI5.7 owns the production boundary.
- Guarded real-clamd clean + EICAR gate:
  `BLOCKED_EXTERNAL_SERVICE_GATE` / `REAL_CLAMD_GATE_BLOCKED`.
- External staging clamd endpoint and health gate remain operator-required.
- Production rollout/acceptance: not authorized.
