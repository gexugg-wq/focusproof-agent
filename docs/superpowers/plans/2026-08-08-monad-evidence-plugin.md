# Monad Learning Evidence Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional Monad plugin that verifies a learner's real call to one predeployed teaching contract, feeds bounded chain facts into the existing OpenHands Conversation, and preserves all domain-general behavior when disabled or removed.

**Architecture:** The plugin registers through the existing capability/tool assembly boundary and contributes one OpenHands tool plus a repository-backed executor. The browser signs the teaching transaction through an injected wallet; the backend performs read-only verification against trusted configuration. Monad facts become native OpenHands Observations, while the existing Agent and scoring flow remain the only decision makers.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, OpenHands SDK 1.31.0 public APIs, optional web3.py backend dependency, Next.js 15, TypeScript, viem, Solidity, Hardhat with its viem plugin, Vitest, pytest and Playwright.

## Global Constraints

- Work only from `/home/holy/web3/focusproof-agent` in WSL/Linux.
- Start from branch `agent/monad-evidence-plugin` and design commit `a08a11a`.
- Read `docs/superpowers/specs/2026-08-08-monad-evidence-plugin-design.md` before editing.
- Reuse OpenHands SDK 1.31.0 Agent, Conversation, EventLog, Tool, ActionEvent and ObservationEvent directly.
- Do not create a second Runtime, Conversation, EventLog, Agent loop, Action/Observation protocol or tool dispatcher.
- Do not add Monad vocabulary or transaction success bonuses to general scoring.
- The plugin is disabled by default and the core must not import it when disabled.
- The backend never receives, stores or logs a wallet private key and never signs a transaction.
- The RPC URL and contract target come only from trusted backend configuration.
- Do not begin multimodal work, generic proof recording, tokens, NFTs or wallet authentication.
- Preserve unrelated user changes and never read or print `.env` secret values.
- Use TDD: observe RED, implement the minimum, observe GREEN, review scope, then commit each task.

## File Map

- `agent-server/focusproof/domain/plugins/base.py`: core-neutral plugin protocol.
- `agent-server/focusproof/domain/plugins/monad/`: all Monad backend ownership.
- `agent-server/focusproof/openhands_runtime/tool_assembler.py`: consumes registered plugin tools without importing Monad.
- `agent-server/focusproof/openhands_runtime/factory.py`: injects enabled plugin providers.
- `agent-server/focusproof/api/app.py`: generic capability and evidence API wiring only.
- `agent-server/focusproof/persistence/models.py`: plugin claim record relationship only if required by the repository pattern.
- `agent-server/migrations/versions/0004_monad_evidence_claims.py`: plugin-owned uniqueness table.
- `contracts/monad-learning-task/`: isolated Solidity package and deployment artifacts.
- `frontend/features/plugins/monad/`: all Monad UI and wallet transaction code.
- `frontend/lib/api/contracts.ts`: generic plugin capability response type.
- `agent-server/tests/plugins/monad/`: deterministic verifier, configuration and removal tests.
- `frontend/tests/monad-plugin.test.tsx`: UI capability and wallet behavior tests.
- `frontend/e2e/monad-learning-flow.spec.ts`: deterministic browser flow.

---

### Task 1: Freeze the optional plugin contract

**Files:**
- Create: `agent-server/focusproof/domain/plugins/base.py`
- Create: `agent-server/focusproof/domain/plugins/monad/__init__.py`
- Create: `agent-server/focusproof/domain/plugins/monad/configuration.py`
- Modify: `agent-server/focusproof/openhands_runtime/tool_assembler.py`
- Modify: `agent-server/focusproof/openhands_runtime/factory.py`
- Test: `agent-server/tests/plugins/test_plugin_boundary.py`
- Test: `agent-server/tests/plugins/monad/test_configuration.py`

**Interfaces:**
- Produces: `EvidencePluginProvider.plugin_id`, `tool_definitions()`, and `executors()`.
- Produces: `MonadPluginSettings.from_environ(environ)` returning disabled settings or a fully validated enabled configuration.
- Consumes: existing OpenHands `ToolDefinition` and `ToolExecutor` types already used by the runtime.

- [ ] **Step 1: Write RED boundary tests**

Test that default startup registers no `monad` capability, does not require any Monad variable, and builds the existing tool set. Add an enabled-configuration test that rejects a missing RPC URL, non-integer chain ID, invalid contract address and secret-bearing explorer URL.

- [ ] **Step 2: Run RED tests**

Run: `.venv/bin/pytest agent-server/tests/plugins/test_plugin_boundary.py agent-server/tests/plugins/monad/test_configuration.py -q`
Expected: collection or import failure because the plugin protocol and settings do not exist.

- [ ] **Step 3: Implement the minimal protocol and settings**

Use immutable dataclasses or Pydantic models. The disabled constructor must return without touching optional web3.py imports. Enabled settings must contain `rpc_url`, `chain_id`, checksummed `contract_address`, `deployment_block` and `explorer_tx_base_url`.

- [ ] **Step 4: Integrate provider injection without a Monad import in core modules**

Pass a sequence of `EvidencePluginProvider` objects from application composition into the existing assembler/factory. An empty sequence must preserve byte-for-byte equivalent tool names and runtime behavior.

- [ ] **Step 5: Run GREEN and boundary regression**

Run: `.venv/bin/pytest agent-server/tests/plugins agent-server/tests/openhands_runtime/test_tool_assembler.py agent-server/tests/openhands_runtime/test_factory.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

Commit: `feat(plugins): add optional evidence plugin boundary`

### Task 2: Build and test the teaching contract

**Files:**
- Create: `contracts/monad-learning-task/package.json`
- Create: `contracts/monad-learning-task/hardhat.config.ts`
- Create: `contracts/monad-learning-task/src/MonadLearningCounter.sol`
- Create: `contracts/monad-learning-task/test/MonadLearningCounter.ts`
- Create: `contracts/monad-learning-task/scripts/deploy.ts`
- Create: `contracts/monad-learning-task/deployments/README.md`
- Modify: `contracts/README.md`

**Interfaces:**
- Produces: ABI containing `counts(address)`, `increment()`, and `Incremented(address,uint256,uint256)`.
- Produces: deployment script output containing only public address, transaction hash, chain ID, compiler version and source commit.

- [ ] **Step 1: Verify current official Hardhat viem setup**

Consult current official Hardhat documentation before selecting compatible package versions. Commit the generated lockfile. Do not use an unmaintained test helper or hand-roll an EVM.

- [ ] **Step 2: Write the contract test before the contract**

Test separate counters per caller, `0 -> 1 -> 2`, exact event arguments, no payable value acceptance, and no function that changes another wallet's counter.

- [ ] **Step 3: Run RED**

Run from `contracts/monad-learning-task`: `npm test`
Expected: compile failure because `MonadLearningCounter.sol` does not exist.

- [ ] **Step 4: Implement the minimal Solidity contract**

Use Solidity `^0.8.24`. `increment()` reads the caller's value, stores value plus one, emits one event and exposes no owner or withdrawal surface.

- [ ] **Step 5: Run GREEN and compile**

Run: `npm test`
Run: `npm run compile`
Expected: all contract tests pass and ABI artifacts are generated.

- [ ] **Step 6: Add a non-secret deployment script**

The script reads RPC and deployer account from process environment at execution time, never writes credentials, waits for the receipt, verifies deployed bytecode and writes a public JSON artifact only after success.

- [ ] **Step 7: Commit**

Commit: `feat(contracts): add Monad learning counter`

### Task 3: Implement deterministic chain verification

**Files:**
- Create: `agent-server/focusproof/domain/plugins/monad/models.py`
- Create: `agent-server/focusproof/domain/plugins/monad/rpc_client.py`
- Create: `agent-server/focusproof/domain/plugins/monad/verifier.py`
- Create: `agent-server/focusproof/domain/plugins/monad/errors.py`
- Modify: `pyproject.toml`
- Test: `agent-server/tests/plugins/monad/fake_rpc.py`
- Test: `agent-server/tests/plugins/monad/test_verifier.py`
- Test: `agent-server/tests/plugins/monad/test_resource_bounds.py`

**Interfaces:**
- Produces: `MonadEvidence(wallet_address, transaction_hash, explanation)`.
- Produces: `MonadVerificationObservation(status, facts, findings, block_number, retryable)`.
- Produces: `MonadEvidenceVerifier.verify(evidence, session_started_at)`.

- [ ] **Step 1: Write the fake RPC and positive RED test**

Represent RPC calls through a narrow `MonadRpcClient` protocol. The positive fixture contains chain ID, transaction, successful receipt, teaching-contract bytecode, block timestamp and one correctly encoded `Incremented` log.

- [ ] **Step 2: Write the negative matrix before implementation**

Add one exact assertion for wrong chain, missing transaction, pending receipt, failed receipt, sender mismatch, contract mismatch, missing bytecode, wrong selector, missing event, event learner mismatch, invalid transition, stale block and deadline exhaustion.

- [ ] **Step 3: Run RED**

Run: `.venv/bin/pytest agent-server/tests/plugins/monad/test_verifier.py agent-server/tests/plugins/monad/test_resource_bounds.py -q`
Expected: import failure for verifier types.

- [ ] **Step 4: Implement web3.py adapter and pure verifier**

Keep decision logic independent of the network adapter. Use fixed contract ABI and selector, fixed allowed RPC methods, request deadline, one bounded retry for retryable transport failures, bounded log count and no user-provided target.

- [ ] **Step 5: Run GREEN and static checks**

Run: `.venv/bin/pytest agent-server/tests/plugins/monad -q`
Run: `.venv/bin/ruff check agent-server/focusproof/domain/plugins/monad agent-server/tests/plugins/monad`
Run: `.venv/bin/mypy agent-server/focusproof/domain/plugins/monad`
Expected: all pass.

- [ ] **Step 6: Commit**

Commit: `feat(monad): verify teaching transactions`

### Task 4: Persist claim uniqueness and project native events

**Files:**
- Create: `agent-server/migrations/versions/0004_monad_evidence_claims.py`
- Modify: `agent-server/focusproof/persistence/models.py`
- Create: `agent-server/focusproof/domain/plugins/monad/repository.py`
- Test: `agent-server/tests/plugins/monad/test_claim_repository.py`
- Test: `agent-server/tests/plugins/monad/test_migration.py`

**Interfaces:**
- Produces: `MonadClaimRepository.claim(chain_id, tx_hash, session_id, evidence_id, observation_event_id)`.
- Guarantees: unique normalized `(chain_id, transaction_hash)` across Sessions and idempotence within the same Evidence.

- [ ] **Step 1: Write RED migration and concurrency tests**

Prove two Sessions cannot claim the same transaction, the same Evidence can retry idempotently, uppercase/lowercase transaction hashes normalize identically, rollback releases no partial claim, and upgrade/downgrade/re-upgrade succeeds on SQLite and emits PostgreSQL-compatible DDL.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest agent-server/tests/plugins/monad/test_claim_repository.py agent-server/tests/plugins/monad/test_migration.py -q`
Expected: missing migration/model failures.

- [ ] **Step 3: Implement the plugin-owned table and repository**

Store only public chain facts and FocusProof identifiers. Do not store explanations, private evidence, RPC URLs or wallet secrets.

- [ ] **Step 4: Run GREEN plus existing migrations**

Run: `.venv/bin/pytest agent-server/tests/plugins/monad agent-server/tests/persistence/test_migrations.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

Commit: `feat(monad): prevent evidence transaction reuse`

### Task 5: Register the official OpenHands tool flow

**Files:**
- Create: `agent-server/focusproof/domain/plugins/monad/tool.py`
- Create: `agent-server/focusproof/domain/plugins/monad/executor.py`
- Modify: `agent-server/focusproof/openhands_runtime/prompts.py`
- Test: `agent-server/tests/plugins/monad/test_openhands_flow.py`
- Test: `agent-server/tests/plugins/monad/test_tool_safety.py`

**Interfaces:**
- Produces tool name: `verify_monad_learning_transaction`.
- Tool parameters: stable `session_id` and `evidence_id` only.
- Executor output: official OpenHands Observation containing bounded JSON facts.

- [ ] **Step 1: Write RED native-event tests**

Assert the enabled Agent tool map contains the fixed tool, the disabled map does not, an SDK ActionEvent reaches the registered executor, the resulting SDK ObservationEvent has the same tool-call ID, and the Conversation pauses/resumes through existing public APIs.

- [ ] **Step 2: Write safety RED tests**

Reject LLM-supplied RPC URL, contract address, wallet override, transaction object or raw ABI. Assert raw evidence is loaded through the owned repository by `session_id/evidence_id`.

- [ ] **Step 3: Run RED**

Run: `.venv/bin/pytest agent-server/tests/plugins/monad/test_openhands_flow.py agent-server/tests/plugins/monad/test_tool_safety.py -q`
Expected: missing tool/executor failure.

- [ ] **Step 4: Implement tool, executor and minimal prompt guidance**

The prompt states that verified chain action is not verified understanding and asks the Agent to test state changes, read/write calls, receipts, events and failure meaning. Do not change scoring weights.

- [ ] **Step 5: Run GREEN and OpenHands boundary regression**

Run: `.venv/bin/pytest agent-server/tests/plugins/monad agent-server/tests/openhands_runtime agent-server/tests/ai4c/test_openhands_reuse_boundary.py -q`
Expected: all non-real-LLM tests pass.

- [ ] **Step 6: Commit**

Commit: `feat(monad): add OpenHands verification tool`

### Task 6: Expose capability and build the optional task UI

**Files:**
- Modify: `agent-server/focusproof/api/app.py`
- Modify: `agent-server/focusproof/api/models.py`
- Modify: `frontend/lib/api/contracts.ts`
- Create: `frontend/features/plugins/monad/contract.ts`
- Create: `frontend/features/plugins/monad/wallet-client.ts`
- Create: `frontend/features/plugins/monad/MonadTaskPanel.tsx`
- Create: `frontend/features/plugins/monad/MonadEvidenceForm.tsx`
- Create: `frontend/features/plugins/monad/MonadVerificationResult.tsx`
- Modify: `frontend/features/session/SessionWorkspace.tsx`
- Test: `agent-server/tests/plugins/monad/test_capability_api.py`
- Test: `frontend/tests/monad-plugin.test.tsx`

**Interfaces:**
- Produces public capability metadata: enabled, chain ID, contract address and explorer transaction base URL; never RPC URL.
- Produces evidence metadata: connected wallet and transaction hash; explanation remains text content.

- [ ] **Step 1: Write API and UI RED tests**

Assert the disabled response omits Monad public configuration, enabled response redacts RPC, general Sessions render no Monad panel, a Monad Session displays the task, wrong network disables submission, wallet rejection preserves state, and a successful wallet request captures the hash without auto-submitting evidence.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest agent-server/tests/plugins/monad/test_capability_api.py -q`
Run from `frontend`: `npm test -- --run tests/monad-plugin.test.tsx`
Expected: missing endpoint/components failure.

- [ ] **Step 3: Implement generic API capability wiring and viem wallet flow**

Use the injected provider through viem, encode only the fixed `increment()` call, display contract/network before confirmation, require a user click, and keep the captured hash editable only by starting a new attempt.

- [ ] **Step 4: Run GREEN, accessibility and production checks**

Run: `.venv/bin/pytest agent-server/tests/plugins/monad/test_capability_api.py -q`
Run from `frontend`: `npm test`, `npm run lint`, `npm run typecheck`, `npm run build`.
Expected: all pass.

- [ ] **Step 5: Commit**

Commit: `feat(frontend): add optional Monad learning task`

### Task 7: Prove the end-to-end learning loop and removability

**Files:**
- Create: `frontend/e2e/monad-learning-flow.spec.ts`
- Create: `agent-server/tests/plugins/monad/test_plugin_removal.py`
- Create: `agent-server/tests/plugins/monad/test_review_scoring_boundary.py`
- Modify: `docs/project-management/TASK_BOARD.md`
- Create: `docs/research/MONAD_EVIDENCE_PLUGIN_REPORT.md`

**Interfaces:**
- Consumes all earlier tasks.
- Produces deterministic acceptance evidence and an explicit testnet-deployment gate.

- [ ] **Step 1: Write deterministic E2E with a fake RPC boundary**

Cover Session creation, Monad task visibility, wallet transaction capture, evidence submission, SDK Action/Observation, awaiting-user question, answer, completed Review and Build Log. Assert the score is not automatically high when the answer is weak despite verified transaction facts.

- [ ] **Step 2: Write physical-removal and disabled-mode tests**

Run a subprocess with the plugin disabled and optional web3.py import blocked. Assert backend health, text evidence, URL evidence, Conversation creation and tool names remain valid. Scan general scoring for `txHash`, `gas`, `nonce`, `Monad` and transaction-success bonus branches.

- [ ] **Step 3: Run focused GREEN**

Run: `.venv/bin/pytest agent-server/tests/plugins/monad -q`
Run from `frontend`: `npx playwright test e2e/monad-learning-flow.spec.ts`.
Expected: all pass.

- [ ] **Step 4: Run full deterministic regression**

Run: `.venv/bin/pytest agent-server/tests -q -m "not real_llm"`
Run: `.venv/bin/ruff check agent-server`
Run: `MYPYPATH=scripts .venv/bin/mypy agent-server scripts`
Run from `frontend`: `npm test`, `npm run lint`, `npm run typecheck`, `npm run build`.
Expected: all pass with only documented pre-existing warnings.

- [ ] **Step 5: Commit deterministic MVP**

Commit: `test(monad): prove learning flow and removability`

### Task 8: Deploy once and perform real Monad acceptance

**Files:**
- Create after confirmed deployment: `contracts/monad-learning-task/deployments/monad-testnet.json`
- Modify: `docs/research/MONAD_EVIDENCE_PLUGIN_REPORT.md`
- Create: `docs/deployment/MONAD_PLUGIN_LOCAL.md`

**Interfaces:**
- Produces public deployment address and transaction links.
- Requires: user-controlled funded testnet wallet and explicit wallet confirmations.

- [ ] **Step 1: Verify live network information from official Monad sources**

Record the official testnet name, chain ID, RPC policy, explorer URL and faucet instructions in the report with links and access date. Do not use remembered values.

- [ ] **Step 2: Stop at the human wallet gate**

Ask the user to fund a dedicated testnet wallet and execute the deployment command locally. Never ask for or accept the private key in chat. The deployer secret exists only in the user's terminal environment for the duration of deployment.

- [ ] **Step 3: Verify deployment before enabling the plugin**

Read bytecode through an independent RPC request, match chain ID and deployment receipt, compare ABI/source commit, and write only the public deployment JSON.

- [ ] **Step 4: Run one real UI transaction**

The user confirms `increment()` in the wallet. Capture the public transaction hash, verify all plugin facts, answer at least two Agent questions, complete Review and inspect Build Log.

- [ ] **Step 5: Run one negative real case without spending another transaction**

Resubmit the same transaction under a second Session and confirm `evidence_reused`; then restore the completed positive Session.

- [ ] **Step 6: Final security and scope review**

Scan Git history and browser bundle for RPC credentials/private keys, verify plugin-disabled regression again, confirm no generic score change, and document residual limitation that sender matching is not FocusProof identity proof.

- [ ] **Step 7: Commit final deployment evidence**

Commit: `docs(monad): record testnet plugin acceptance`

## Execution Checkpoints

- Checkpoint A after Task 2: contract behavior and plugin boundary review.
- Checkpoint B after Task 5: independent OpenHands reuse and security review.
- Checkpoint C after Task 7: deterministic MVP can be accepted without wallet access.
- Checkpoint D after Task 8: real testnet acceptance and final AI0 decision.

The worker must stop and ask AI0 one question at a time whenever a design ambiguity affects public interfaces, security claims, migrations or OpenHands protocol reuse. The worker may continue independently for ordinary implementation details once confidence is at least 90 percent.
