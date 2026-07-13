# OpenHands Reuse Strategy

Version: v0.4
Primary runtime language: Python
Product scope: general learning verification, with Web3 as the first domain plugin

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
3. Build a FocusProof adapter layer around OpenHands Conversation, ConversationState, EventLog, Agent, Tool and event objects.
4. Promote OpenHands Conversation from debug spike into the official `/sessions/{id}/review` orchestration path.
5. Keep FocusProof-owned learning models at the product boundary: Evidence, ReviewResult, scoring dimensions and proof payload.
6. Do not reimplement a full local mirror runtime unless a specific OpenHands SDK class is impossible to instantiate or adapt.

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
  -> FocusProof OpenHands Conversation adapters
  -> OpenHands SDK Conversation / ConversationState / EventLog / View
  -> OpenHands SDK Agent.step
  -> OpenHands SDK ActionEvent / ToolDefinition / ToolExecutor / ObservationEvent
  -> FocusProof evidence tools and scoring
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

FocusProof may translate OpenHands events into FocusProof learning events, but it must not keep two unrelated ledgers forever. The desired state is:

```text
OpenHands EventLog is the runtime ledger.
FocusProof EventLog is the product/audit projection of that runtime ledger.
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
