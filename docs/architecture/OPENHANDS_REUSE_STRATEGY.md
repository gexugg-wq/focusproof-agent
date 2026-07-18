# OpenHands Reuse Strategy

Version: v0.7
Primary runtime language: Python
Product scope: general learning verification; optional Web3 plugin deferred

Accepted runtime baseline: AI4B at `bf5c9a8` on OpenHands SDK 1.31.0. AI4C must
consume the accepted Conversation/tool/event boundary and must not redesign the
Agent runtime. DashScope real-provider acceptance uses the SDK LLM/LiteLLM
integration and remains provider-neutral. OIDC, admission and product logging
are FocusProof policy boundaries, not alternate Conversation orchestration.

The AI4C gap audit is limited to:

- the existing bounded URL execution pool for process-wide blocking-I/O limits;
- the SDK 1.31.0 `Conversation` factory not forwarding the public
  `LocalConversation.max_budget_per_run` option;
- FocusProof-wide or per-principal provider admission outside the SDK's
  per-LLM/run controls;
- FocusProof identity and product-data redaction, which the SDK cannot define.

Each local addition must remain minimal, use public SDK types and lifecycle,
and include a removal condition. It must not schedule Agent steps, own
Conversation state or create another runtime.

AI4C Repair 3 removed the earlier deterministic learning Agent and Conversation
fallback. The only executable review loop and native runtime fact ledger are
the official OpenHands SDK `Conversation` and its native EventLog. FocusProof
stores only a read/query audit projection through `AuditProjection` and
`AuditQuery`; neither the in-memory nor persistent projection store can run an
Agent, execute a tool or restore native runtime state.

## 1. Why This Changes the Plan

The original v0.1 plan treated OpenHands mainly as an architecture reference and planned a TypeScript-first runtime. That is still viable for a quick web demo, but it wastes the main advantage of OpenHands SDK: its existing Python agent runtime concepts and tool protocol.

FocusProof should therefore use a hybrid architecture:

- Frontend: Next.js and TypeScript.
- Agent Server: Python and FastAPI.
- Runtime: Python, directly integrating OpenHands SDK Conversation/State/Event mechanics.
- Tools: Python tool executors.
- Database: SQLite for demo, PostgreSQL-compatible schema later.
- Smart contract: Solidity on Monad Testnet.

## 2. What We Must Reuse From OpenHands

The OpenHands SDK is not only a source of names or interface inspiration. Its most important value is the runtime flow:

```text
Conversation factory
-> LocalConversation / RemoteConversation
-> ConversationState / EventLog / View
-> LocalConversation.run()
-> Agent.step()
-> LLM response dispatch
-> ActionEvent
-> ToolDefinition / ToolExecutor
-> ObservationEvent
-> EventLog / View for the next step
```

FocusProof must align to this flow.

Reusable mechanics:

- Agent abstraction: one step receives context and returns a decision.
- Conversation factory: unified entrypoint that hides local/remote execution differences.
- LocalConversation: the actual local run container for the learning review loop.
- ConversationState: the state owner for execution status, active branch, EventLog and View.
- EventLog: the append-only fact ledger, not a UI timeline and not a temporary list.
- View: the derived active-branch context that the Agent/LLM sees.
- Tool protocol: tools are callable units with input schema and structured results.
- ActionEvent and ObservationEvent separation: the agent requests work, tools return observed facts, and both go back into the event ledger.
- Agent server boundary: the runtime should be callable from a frontend/backend boundary without exposing model secrets to the browser.

These parts help FocusProof avoid a polluted agent loop. The learning judge should not directly perform tool work; it should request tool work, receive observations, and continue from the updated ConversationState/View.

### 2.1 Non-Negotiable Direct-Reuse Rule

When OpenHands SDK already exposes a suitable public type, method, lifecycle hook or protocol, FocusProof must use it directly. The project must not build an "OpenHands-inspired" mirror merely because a local implementation appears smaller or easier to control.

This rule applies at minimum to:

- `Agent` and `Agent.step()`;
- `Conversation`, `LocalConversation`, `ConversationState` and the native EventLog/View;
- `MessageEvent`, `ActionEvent`, `ObservationEvent` and other native runtime events;
- Action, Observation, `ToolDefinition`, `ToolExecutor` and tool registration;
- run, pause, interrupt, cancellation, close and recovery lifecycle behavior;
- native event callbacks, event restoration and agent-server boundaries;
- SDK security and redaction utilities when they satisfy the product requirement.

Every implementation decision must follow this order:

1. Pin and inspect the installed OpenHands SDK version.
2. Search its public API, source and tests for the required behavior.
3. Import and use the public SDK capability directly.
4. Add only a thin FocusProof adapter for product semantics, policy, persistence projection or API translation.
5. Implement FocusProof-owned runtime behavior only after recording an SDK gap.

An SDK gap record must state the installed version, inspected public APIs, why they cannot satisfy the requirement, the smallest local implementation required, tests proving the boundary, and a removal or migration plan. "More convenient", "simpler for now" and "OpenHands-style" are not valid gap justifications.

Prohibited when an SDK equivalent exists:

- a second Agent loop, Conversation, ConversationState, EventLog or View implementation;
- local mirror classes for native Action, Observation, Event or Tool protocols;
- a second tool registry, executor lifecycle or cancellation protocol;
- copying OpenHands source into this repository or patching SDK internals;
- mutating private SDK state to simulate a missing public operation.

FocusProof may own learning-specific behavior that OpenHands does not provide: evidence schemas, learning-domain capability policy, scoring, learner ownership and authorization, database projections, Build Log and proof payloads, and stricter security rules such as URL path redaction. These extensions must compose with the native runtime rather than replace it.

When the pinned SDK is upgraded, all gap records and adapters must be re-audited. Any local behavior now covered by a public SDK API must be deleted and migrated to the SDK implementation.

## 3. What We Should Not Reuse Directly

Do not fork the full OpenHands product as the FocusProof base. The product goals are different:

- OpenHands is optimized for software task execution.
- FocusProof is optimized for learning evidence verification.

Do not enable programming-agent tools by default:

- TerminalTool and FileEditorTool are not safe default tools for general learning verification.
- They may become part of a future ProgrammingLearningPlugin.

Do not use OpenHands task completion logic as FocusProof scoring:

- A completed software task is not the same thing as credible learning.
- FocusProof scoring must be based on goal clarity, evidence specificity, goal alignment, understanding, output and reflection.

Do not let tool observations become final judgment:

- A transaction hash can prove an interaction happened.
- It cannot prove the learner understood the transaction.
- Understanding still requires explanation, questioning and consistency checks.

## 4. Direct-Use Strategy

Product decision: FocusProof should directly use OpenHands SDK for the core agent runtime path. This project is not a throwaway demo, so the first backend implementation should avoid building a parallel runtime that later needs to be replaced.

Implementation order:

1. Add OpenHands SDK as a local path dependency from the existing local SDK source.
2. Import and inspect the actual SDK classes used for Agent, Conversation, Tool, Action, Observation and Event.
3. Bind FocusProof product policy to the public OpenHands Conversation, Agent,
   Tool and event APIs without mirroring their runtime types.
4. Promote OpenHands Conversation from debug spike into the official `/sessions/{id}/review` orchestration path.
5. Keep FocusProof-owned learning models at the product boundary: Evidence, ReviewResult, scoring dimensions and proof payload.
6. Do not reimplement a local mirror runtime. If a required public capability is absent, follow the SDK gap process in section 2.1 and add only the smallest product-owned extension.

AI2 must produce an integration report:

- OpenHands SDK dependency method.
- Imported SDK classes or protocols.
- Adapter classes created by FocusProof.
- SDK parts intentionally disabled.
- Remaining blockers.

The expected pattern is not "raw OpenHands everywhere". The expected pattern is:

```text
FocusProof API
  -> FocusProof learning models
  -> FocusProof factory/manager policy
  -> OpenHands SDK Conversation / ConversationState / EventLog / View
  -> OpenHands SDK Agent.step
  -> OpenHands SDK ActionEvent / ToolDefinition / ToolExecutor / ObservationEvent
  -> FocusProof evidence tools and scoring
  -> FocusProof read/query audit projection
```

The debug-only real LLM path is not enough. It proves connectivity, but it does not satisfy the architecture until official session review is driven by a Conversation-backed runtime.

## 5. FocusProof Boundary

The FocusProof-owned boundary is:

- Event schema.
- Evidence schema.
- Learning domain plugin interface.
- Scoring dimensions.
- Review status.
- Build Log generation.
- Proof record payload.

OpenHands should provide runtime mechanics, but it must not define what counts as learning.

FocusProof translates approved OpenHands events into product learning/audit
events for API queries and durable reporting. That store is a projection, not a
second runtime ledger:

```text
OpenHands EventLog is the runtime ledger.
FocusProof AuditProjectionStore is a read/query projection of that ledger.
```

## 6. General Learning Requirement

FocusProof is not a Web3-only verifier. Web3 is the first plugin because it has strong external evidence such as transaction hashes and contract addresses.

The core runtime must support any knowledge domain:

- Programming.
- Math.
- Language learning.
- Reading.
- Research.
- Web3.
- Course study.
- Exam preparation.

Each domain plugin may define:

- Accepted evidence types.
- Domain-specific verification tools.
- Question templates.
- Evidence normalization rules.
- Scoring hints.

The final review protocol stays shared across all domains.
