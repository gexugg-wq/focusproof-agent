# Monad Learning Evidence Plugin Design

Date: 2026-08-08
Status: approved
Owner: AI0

## Objective

Add an optional Monad learning-evidence verification plugin to FocusProof. A learner connects an injected wallet, calls one predeployed teaching contract, submits the wallet address, transaction hash and explanation, and receives objective chain verification followed by OpenHands-backed questions and the existing learning score.

The plugin proves chain facts. It does not independently prove understanding, assign a score, authenticate a FocusProof user, record a generic score on-chain, or become a dependency of the general learning flow.

## Product Boundary

The accepted first task is:

1. Read the learner's counter from a configured teaching contract.
2. Call `increment()` from an injected wallet on the configured Monad network.
3. Capture the transaction hash.
4. Submit the connected wallet address, transaction hash and an operation explanation.
5. Verify the transaction and contract facts through a backend-only RPC client.
6. Return bounded facts through the official OpenHands Action/Observation tool flow.
7. Ask understanding questions in the same Conversation.
8. Produce the existing structured Review and Build Log.

Out of scope:

- generic on-chain proof recording;
- arbitrary contract or ABI verification;
- user-supplied RPC endpoints or contract addresses;
- backend transaction signing, private keys or custodial wallets;
- wallet-based FocusProof login;
- NFT, token, streak or reward issuance;
- images, PDFs, audio or other multimodal evidence;
- copying or replacing OpenHands Runtime, Conversation, EventLog, Agent loop, Action, Observation or Tool protocols.

## Teaching Contract

`MonadLearningCounter` contains `mapping(address => uint256) public counts`, an `increment()` function, and an indexed `Incremented(learner, previousValue, newValue)` event. `increment()` changes only the caller's counter and emits exactly one event.

The contract is tested locally and deployed once to the selected Monad test network. Deployment artifacts contain the network identifier, contract address, deployment transaction hash, ABI, compiler version and source commit. No deployer private key is committed or handled by the application backend.

## Architecture

The plugin lives behind the existing capability registry and is disabled by default. Core modules must not import the Monad package. Plugin configuration causes a provider to register a Monad evidence tool and executor; disabling registration removes the capability.

Suggested ownership:

```text
agent-server/focusproof/domain/plugins/monad/
  manifest.py
  models.py
  configuration.py
  rpc_client.py
  verifier.py
  tool.py
  executor.py
  errors.py

frontend/features/plugins/monad/
  MonadTaskPanel.tsx
  MonadEvidenceForm.tsx
  MonadVerificationResult.tsx
  wallet-client.ts
  contract.ts

contracts/monad-learning-task/
  src/MonadLearningCounter.sol
  test/MonadLearningCounter.t.sol
  script/Deploy.s.sol
  deployments/
```

Use `web3.py` as an optional backend dependency for Ethereum RPC types and receipt/log decoding. Use the existing `viem` dependency in the frontend. Optional dependencies must not be imported when the plugin is disabled.

## Configuration

The plugin defaults to disabled. Required enabled configuration:

```text
FOCUSPROOF_PLUGIN_MONAD_ENABLED=true
FOCUSPROOF_MONAD_RPC_URL=<backend only>
FOCUSPROOF_MONAD_CHAIN_ID=<integer>
FOCUSPROOF_MONAD_CONTRACT_ADDRESS=<checksummed address>
FOCUSPROOF_MONAD_DEPLOYMENT_BLOCK=<integer>
FOCUSPROOF_MONAD_EXPLORER_TX_BASE_URL=<public display base>
```

Startup fails closed for the plugin if enabled configuration is incomplete or inconsistent. Core startup remains valid when the plugin is disabled. The browser never receives a secret-bearing RPC URL.

## Verification Contract

The LLM calls the tool with stable FocusProof identifiers, not raw RPC or arbitrary contract parameters. The executor loads owned evidence from repositories and verifies:

- configured chain ID matches RPC chain ID;
- transaction and receipt exist;
- receipt status is successful;
- transaction sender equals the submitted wallet address;
- transaction target equals the configured teaching contract;
- configured contract has non-empty bytecode;
- calldata selector is exactly `increment()`;
- receipt contains the expected `Incremented` event;
- event learner equals the transaction sender;
- `newValue == previousValue + 1`;
- block timestamp is not earlier than the learning Session, with a documented clock-skew allowance;
- normalized `(chain_id, tx_hash)` has not been claimed by another learning Session.

The Observation is bounded structured data with status, verified facts, findings, safe block metadata and retryability. It never contains RPC credentials and never assigns a learning score.

## OpenHands Integration

Reuse OpenHands SDK 1.31.0 public Agent, Conversation, Tool, ActionEvent and ObservationEvent types directly. The plugin contributes a tool definition and executor through the existing registry and assembler. It may not mutate Conversation state or EventLog directly. The executor returns an Observation, and the existing runtime persists and projects the native event.

The Agent uses verified facts to ask questions about state-changing versus read-only calls, gas, receipts, events, reverts and evidence limits. A successful transaction is evidence of an action, not sufficient evidence of understanding.

## Persistence

Plugin-owned persistence records the normalized chain ID, transaction hash, Session ID, Evidence ID, verification outcome and native Observation event ID. A database uniqueness constraint prevents reuse across Sessions. The table is optional and no core query depends on it. Removing the plugin may leave an inert table without affecting core migrations or runtime behavior.

## Frontend

A capability endpoint determines whether the Monad task is visible. The general Session, text evidence, URL evidence, review and Build Log screens remain unchanged.

The task UI displays the configured network and contract, reads the connected wallet and counter, asks the learner to confirm `increment()`, captures the transaction hash, then submits evidence. The wallet must show its own confirmation. The application never requests a private key, never auto-signs and never represents sender matching as FocusProof identity verification.

## Failure Semantics

RPC timeout, provider failure or temporary receipt absence produce `verification_unavailable` or `verification_pending` and do not classify the learner as deceptive. Wrong network, failed receipt, sender mismatch, contract mismatch, missing code, wrong function, missing event, invalid state transition and reused evidence produce explicit non-secret findings.

All RPC operations have deadlines, bounded retries and response-size limits. Contract and chain targets come only from trusted configuration. No user-controlled RPC forwarding or arbitrary `eth_call` is allowed.

## Acceptance

Acceptance requires:

- local contract tests and a recorded testnet deployment;
- one real successful wallet transaction through the product UI;
- positive verification of all required facts;
- negative tests for wrong chain, failed transaction, wrong sender, wrong contract, missing code, wrong selector, missing event, stale transaction, duplicate transaction and RPC failure;
- native OpenHands Action/Observation events in the same Conversation;
- a follow-up answer loop and completed Review/Build Log;
- no score awarded solely because a transaction succeeded;
- no secrets in Git, browser bundles, logs or Observations;
- all existing domain-general tests passing with the plugin disabled;
- a removal test proving the backend starts and text/URL/real-LLM paths remain valid when the plugin package is not registered.

## Delivery Rule

Finish a deterministic local implementation before a real testnet transaction. Network identifiers, official RPC and explorer values must be verified from current official Monad sources at deployment time and must not be guessed or hardcoded from memory.
