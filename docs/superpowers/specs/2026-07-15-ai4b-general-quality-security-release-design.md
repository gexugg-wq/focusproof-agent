# AI4B General Quality, Security & Release Design

Status: Frozen for implementation
Date: 2026-07-15
Owner: AI0 acceptance, AI4B implementation
Baseline: `8c04372`
Branch: `ai4b-general-quality-security-release`

## 1. Purpose

AI4B is the general product quality gate and release-preparation phase for the
existing FocusProof learning-verification system. It validates and hardens the
current product; it does not add a new product domain or replace approved
architecture.

The accepted test boundary is a real FastAPI application, real database, real
frontend BFF, and the real OpenHands SDK runtime driven by deterministic
scripted `TestLLM` responses. Default tests must not read or spend a real LLM
key. A real-LLM smoke test remains gated on separate AI0 authorization.

## 2. Frozen Scope

AI4B may add tests, fixtures, test harnesses, scripts, security and deployment
documentation, research evidence, and small production fixes demonstrated by a
failing test.

AI4B must not:

- implement Solidity, Monad, wallet transactions, Web3 RPC, or on-chain proof;
- implement OCR, ASR, image, audio, video, or PDF processing;
- create another Agent runtime, agent loop, Conversation, EventLog, or tool
  protocol;
- enable OpenHands default programming tools;
- rewrite deterministic scoring or change public scoring rules;
- select an authentication vendor or claim the development identity is
  production authentication;
- change public architecture or protocol documents without AI0 approval;
- modify `contracts/`, `.env`, `var/`, or OpenHands SDK source;
- publish, push, merge, or perform a real public deployment.

The acceptance matrix below is frozen. New findings may result in a narrowly
tested repair, a documented residual risk, or an AI0 decision request. They do
not silently expand this phase.

## 3. Architecture Invariants

The production execution path remains:

```text
Browser
  -> same-origin Next.js BFF
  -> FastAPI session/evidence/answer/review API
  -> FocusProof repositories and SQLite
  -> OpenHands Agent + LocalConversation
  -> native ActionEvent
  -> native ToolExecutor
  -> native ObservationEvent
  -> native EventLog/View and ConversationState
  -> FocusProof ReviewResult projection
  -> browser Review and Build Log projections
```

AI4B tests must prove that path directly. Test-only LLM scripting supplies
model responses, but it must not emulate or bypass `Agent`,
`LocalConversation`, `ToolExecutor`, event creation, persistence, or recovery.

The following remain authoritative:

- OpenHands EventLog is runtime history truth;
- Action and Observation events are OpenHands-native events;
- product tables are projections and product records, not a parallel runtime
  ledger;
- verification tools return observations and never final learning scores;
- the FocusProof scoring layer, rather than a tool or LLM, produces final
  deterministic scores;
- the browser never receives an LLM provider key.

## 4. Acceptance Harness

### 4.1 Layers

The suite uses four complementary layers:

1. Focused backend tests exercise validation, ownership, idempotency, URL
   policy, runtime recovery, interruption, shutdown, and failure injection.
2. Real integration scenarios exercise FastAPI, SQLite, OpenHands SDK
   conversation execution, native event history, and ReviewResult projection
   for the four required learning domains.
3. Frontend tests exercise BFF sanitization and UI behavior such as input
   preservation, state rendering, and XSS-safe output.
4. Playwright starts a test-configured real FastAPI process and real Next.js
   process, then performs the user flow through the BFF without intercepting
   the core FocusProof API.

Existing mock-browser tests may remain as fast UI regression tests, but they do
not count as proof of the required end-to-end path.

### 4.2 Deterministic Real-Runtime Server

A test-only server entry point will:

- create a temporary SQLite database outside tracked `var/`;
- apply the real Alembic head before serving;
- call the production `create_app` factory;
- inject an OpenHands SDK `TestLLM`/scripted LLM factory;
- use the production repositories, conversation manager, tools, and routes;
- bind only to loopback on a test port;
- expose no secret and require no provider credential;
- stop cleanly and delete only its own temporary state.

The harness may restart the FastAPI process against the same temporary database
to prove recovery of the same session and Conversation. It may not implement a
test-only Conversation, EventLog, tool executor, or success shortcut.

### 4.3 Stable Fixtures

Fixtures are deterministic and domain-neutral at the framework level:

| Domain | Evidence | Required signal |
| --- | --- | --- |
| Programming | notes, code explanation, error reflection | explanation and corrected reasoning are distinguishable from a copied goal |
| Mathematics | solution steps, concept explanation | intermediate reasoning is visible; elapsed time is not proof |
| Language | writing sample, self-correction | concrete correction can improve support |
| Reading | summary, quotation, structured recall | source relationship and recall structure are represented |

URL fixtures use a loopback fixture server only in tests where the product's
explicit local-test policy permits it. Production URL policy remains deny by
default for loopback, private, link-local, metadata, and unsafe redirect
targets.

## 5. Frozen Acceptance Matrix

### 5.1 End-to-End Product Flow

| ID | Requirement | Primary evidence |
| --- | --- | --- |
| E2E-01 | Create an arbitrary-domain session through the BFF | real Playwright flow and backend integration test |
| E2E-02 | Submit text and URL evidence | real Playwright flow with deterministic URL fixture |
| E2E-03 | Agent emits native ActionEvent | EventLog assertion and Build Log assertion |
| E2E-04 | ToolExecutor emits native ObservationEvent after its ActionEvent | EventLog type/order assertion |
| E2E-05 | Agent asks a follow-up and accepts the learner answer | API/UI state and event assertions |
| E2E-06 | ReviewResult and ordered Build Log are produced | API, database, and UI assertions |
| E2E-07 | Browser refresh restores product state | Playwright reload assertion |
| E2E-08 | FastAPI restart restores the same Conversation and history | process restart integration assertion |
| E2E-09 | Replay/duplicate requests do not duplicate evidence, runtime events, or reviews | sequential and concurrent tests |
| E2E-10 | Programming, mathematics, language, and reading fixtures execute through the same framework | parameterized real-runtime tests |

### 5.2 Quality Behavior

Quality tests assert conservative behavior and invariants, not new score
thresholds. If an approved score cannot satisfy an invariant without changing
the scoring implementation or public rule, AI4B records the failing fixture and
stops that repair for AI0.

| ID | Scenario | Acceptance |
| --- | --- | --- |
| Q-01 | vague notes | no high-confidence supported result |
| Q-02 | evidence copies the goal | not treated as valid independent evidence |
| Q-03 | evidence mismatches the goal | mismatch is surfaced in findings or result |
| Q-04 | error record plus correct reflection | may provide valid support without hiding the original error |
| Q-05 | strong follow-up answer | result support can improve relative to unanswered/weak input |
| Q-06 | time spent only | never sufficient proof of effective learning |

#### Q-03 Semantic-Association Residual Risk

AI0 accepted the Q-02 copied-goal boundary at `fa11900`. Q-03 remains a
separate residual risk: current English word intersection and Chinese character
intersection are low-confidence heuristics only. They do not prove that a
detailed item is semantically related to the learning goal.

AI4B must not continue adding stop words, character thresholds, or lexical
similarity rules to claim semantic understanding. Public release remains
blocked from claiming reliable detection of every detailed-but-semantically-
unrelated false-learning submission until real Agent/LLM semantic assessment
is integrated with deterministic scoring boundaries and accepted by AI0.

### 5.3 Security

| ID | Threat | Acceptance |
| --- | --- | --- |
| SEC-01 | cross-owner access | all session, evidence, event, answer, and review paths deny it consistently |
| SEC-02 | user text forges Action, Observation, or Review | text remains untrusted evidence/message content and creates no privileged event/result |
| SEC-03 | LLM claims a tool fact without observation | no authoritative tool fact or successful review is synthesized from the claim |
| SEC-04 | prompt injection overrides rules | tool allowlist, reference lookup, and scoring boundaries remain enforced |
| SEC-05 | SSRF via URL, redirect, DNS change, timeout, or sensitive address | request is denied or fails safely without sensitive URL leakage |
| SEC-06 | XSS/rich text payload | rendered as inert text; no executable markup or dangerous URL behavior |
| SEC-07 | oversized input/request/resource use | bounded before expensive runtime work and reported safely |
| SEC-08 | replay/concurrent review | one logical review execution/result, deterministic conflict behavior, no duplicate events |
| SEC-09 | internal exception | response excludes paths, SQL, secrets, traces, and raw evidence |
| SEC-10 | client bundle/config | no LLM secret is exposed to browser code or BFF responses |
| SEC-11 | logs, reports, screenshots | automated/static inspection finds no credential or raw secret fixture |

The current fixed development identity (`dev-anonymous-user`) is acceptable
only for local/staging-isolated development. Public deployment is blocked until
AI0 approves a production identity and authorization design. AI4B will document
and test the present ownership mechanism but will not choose OAuth, Auth0, or
another provider.

### 5.4 Reliability and Recovery

| ID | Failure/recovery case | Acceptance |
| --- | --- | --- |
| REL-01 | SQLite and Alembic lifecycle | head is checked; upgrade, rollback, re-upgrade, backup, and restore procedures are verified |
| REL-02 | Conversation close/server restart | same session uses restored native history without rewriting events |
| REL-03 | review interrupt/timeout/cancel/retry | safe terminal/intermediate state and one recoverable retry path |
| REL-04 | same-session concurrency | serialized or rejected with explicit busy semantics |
| REL-05 | different-session concurrency | progresses independently without global serialization |
| REL-06 | database unavailable | no false success; sanitized service response |
| REL-07 | LLM unavailable | no completed review/result; retry remains possible |
| REL-08 | tool unavailable | failed/inconclusive observation is explicit; no invented success |
| REL-09 | frontend/BFF network failure | visible error, preserved user input, retry possible |
| REL-10 | shutdown | new reviews are rejected and conversations/providers/DB resources close cleanly |

### 5.5 Deployment Readiness

| ID | Deliverable | Acceptance |
| --- | --- | --- |
| DEP-01 | local WSL guide | clean startup, migrations, frontend/server URLs, and shutdown |
| DEP-02 | staging guide | vendor-neutral topology, environment names, CORS/proxy boundary, smoke procedure |
| DEP-03 | operations guide | health/readiness interpretation, logging, backup, recovery, rollback, diagnosis |
| DEP-04 | threat model | assets, trust boundaries, actors, threats, controls, residual risks |
| DEP-05 | security acceptance | maps SEC cases to automated/manual evidence and blockers |
| DEP-06 | scripts | safe startup/check/smoke helpers with no embedded secrets or public deployment |
| DEP-07 | `.env.example` | variable names and safe explanatory placeholders only |
| DEP-08 | public release decision | explicitly blocked by development-only identity until AI0 decision |

### 5.6 Browser and Visual Acceptance

The same general learning flow is checked at `1440x900`, `1280x720`,
`390x844`, and `360x800`.

| ID | Requirement | Evidence |
| --- | --- | --- |
| VIS-01 | no element overlap, horizontal page overflow, or clipped required text | Playwright geometry assertions and screenshots |
| VIS-02 | input survives failed submission | network-failure scenario at desktop and mobile |
| VIS-03 | `awaiting_user`, `completed`, and `failed` are visually distinct | state assertions and screenshots |
| VIS-04 | Build Log is chronological and Action precedes Observation | DOM and event timestamp/order assertion |
| VIS-05 | wallet/proof recording is not required | complete flow without either control |
| VIS-06 | screenshots contain no secret | files under `docs/research/assets/ai4b/` plus secret scan |

## 6. Idempotency Design

Duplicate protection is an API/repository concern and must not introduce a new
runtime ledger.

- Evidence creation uses a stable request identity when one exists, otherwise a
  deterministic server fingerprint over the owned session and normalized
  request content within the accepted operation contract.
- Duplicate evidence returns the existing logical record and does not send a
  second synchronization message.
- Review uses the existing per-session execution guard and persisted review
  state. Sequential or concurrent replay must return the existing result or a
  safe in-progress/busy response rather than start a second run.
- Answer replay must not append the same logical learner response twice.
- Database uniqueness is the final concurrency guard; in-memory locks are not
  accepted as the only cross-request protection.
- Native OpenHands events are never deleted or rewritten to hide duplication.

If implementing this invariant requires adding a new public request field,
changing response shape, or changing the documented protocol, implementation
stops for AI0. Internal deterministic keys and existing response semantics are
preferred.

## 7. Input and Output Security Design

Validation is applied before database writes, URL access, or LLM execution:

- bounded goal, domain, evidence, answer, URL, and metadata sizes;
- bounded JSON request body at the ASGI boundary where practical;
- normalized URL parsing with credentials and unsafe schemes rejected;
- DNS results checked before connection and every redirect checked again;
- strict fetch timeout and response-size/content limits;
- safe generic API errors and structured internal logging with redaction;
- React text rendering retained; no `dangerouslySetInnerHTML` for model,
  evidence, finding, or Build Log content;
- BFF forwards only allowlisted routes/methods and never provider credentials.

Security fixtures use unmistakably fake sentinel credentials. Tests scan
responses, logs, generated reports, and screenshots for those sentinels and for
common secret formats. Raw learner evidence is not copied into operational
error messages or release reports.

## 8. Failure-State Design

No layer may convert uncertainty or infrastructure failure into success:

- an LLM exception cannot create a completed ReviewResult;
- a tool error is a failed/inconclusive Observation, not verified evidence;
- a database failure cannot return a created/reviewed success response;
- BFF timeout/network failure is visible and preserves pending user input;
- cancellation and shutdown leave a recoverable state and close SDK resources;
- retry reuses persisted history and does not fabricate or replay successful
  tool observations.

Health and readiness documentation must describe the current route semantics
accurately. A new public health protocol will not be invented in AI4B; any
missing production readiness contract is recorded as a deployment blocker or
sent to AI0.

## 9. Documentation and Release Evidence

AI4B produces:

- `docs/security/THREAT_MODEL.md`;
- `docs/security/SECURITY_ACCEPTANCE.md`;
- `docs/deployment/LOCAL_WSL.md`;
- `docs/deployment/STAGING.md`;
- `docs/deployment/OPERATIONS.md`;
- required safe scripts under `scripts/`;
- visual evidence under `docs/research/assets/ai4b/`;
- `docs/research/AI4B_GENERAL_QUALITY_SECURITY_RELEASE_REPORT.md`.

The final report maps every matrix row to exact test names, commands, or manual
evidence. It distinguishes repaired defects, accepted local-only constraints,
unresolved risks, scoring findings requiring AI0, and public deployment
blockers.

## 10. Gate Commands

The release candidate is not accepted until all authorized gates pass from a
cleanly understood worktree:

```bash
pytest agent-server/tests -q -m "not real_llm"
ruff check agent-server
mypy agent-server
cd frontend && npm run lint
cd frontend && npm run typecheck
cd frontend && npm run test
cd frontend && npm run build
cd frontend && npm run test:e2e
cd frontend && npm audit --omit=dev
git diff --check
git status --short --branch
```

The npm audit result is a gate, not an informational warning. Dependency repair
must be minimal, compatible, and validated by the full frontend gate. No real
LLM smoke is part of the default gate.

## 11. Stop Conditions

Implementation stops and requests AI0 direction if evidence shows that
acceptance requires any of the following:

- a public API/event/tool protocol or architecture change;
- a change to deterministic scoring logic or public score meaning;
- a production identity-provider choice or authorization architecture;
- weakening OpenHands reuse or adding a parallel runtime truth source;
- adding Web3, multimodal, or unrelated product scope.

When all matrix rows and gates have evidence, AI4B stops after local commits and
the final report. It does not push, merge, deploy publicly, or proceed to a
later phase.
