# Monad plugin local testnet deployment

Verified on 2026-08-08. Recovery evidence recorded on 2026-08-09. This procedure stops at a user-controlled wallet boundary. FocusProof never receives, stores, logs, or uses the deployer private key, and the backend never signs transactions.

## Verified public network information

- Network: Monad Testnet
- Chain ID: `10143` (`0x279f`)
- Native token: `MON`
- Public RPC used for read-only acceptance and recovery: `https://testnet-rpc.monad.xyz/`
- Faucet: `https://faucet.monad.xyz/`
- Official network documentation, accessed 2026-08-08: `https://docs.monad.xyz/developer-essentials/network-information`
- Official JSON-RPC reference, accessed 2026-08-08: `https://docs.monad.xyz/reference/json-rpc/api`

The 2026-08-08 read-only probe returned chain ID `10143` and latest block `51987353`. Recheck both immediately before deployment because live network state changes.

## HUMAN_WALLET_GATE prerequisites

Prepare a dedicated Monad Testnet wallet in your own WSL terminal. Fund it with testnet MON from the official faucet. Never paste its private key into chat, project files, `.env`, command arguments, shell history, logs, or screenshots. The deployment command must run only after the wallet shows the expected address and testnet balance.

The contract toolchain requires Node.js 22.13.0 or newer. From the repository root, first verify the contract package:

```bash
cd /home/holy/web3/focusproof-agent/contracts/monad-learning-task
node --version
npm test
npm run compile
npm run typecheck
```

Expected: Node 22.13.0 or newer, passing tests, successful compilation, and no TypeScript errors.

## User-only deployment command

Run this in your own interactive WSL terminal. `read -s` keeps the private key off screen and out of the command line. The output artifact is created only after a successful receipt, matching contract address, Monad Testnet chain ID, and non-empty deployed bytecode.

```bash
cd /home/holy/web3/focusproof-agent/contracts/monad-learning-task
read -rsp "Dedicated Monad Testnet deployer private key: " MONAD_DEPLOYER_PRIVATE_KEY
printf "\n"
export MONAD_DEPLOYER_PRIVATE_KEY
export MONAD_RPC_URL=https://testnet-rpc.monad.xyz/
export MONAD_DEPLOYMENT_OUTPUT=deployments/monad-testnet.json
npx hardhat run scripts/deploy.ts --network monad
unset MONAD_DEPLOYER_PRIVATE_KEY MONAD_RPC_URL MONAD_DEPLOYMENT_OUTPUT
```

If deployment fails or is interrupted, immediately run the same `unset` command. Do not retry until `deployments/monad-testnet.json` is absent and the failure is understood; artifact creation uses exclusive-write semantics and will not overwrite an existing file.

Expected successful output: `Deployment metadata written to .../deployments/monad-testnet.json`. The JSON is public and contains only `contractAddress`, `deploymentTransactionHash`, `chainId`, `compilerVersion`, `sourceCommit`, and `abi`. It must not contain an RPC URL, private key, or wallet secret.

## Recovery after RPC propagation lag

A deployment transaction can be broadcast and mined before the RPC node used by Hardhat/viem can immediately serve `eth_getTransaction` or related follow-up reads. If the deploy command shows a transaction hash and then fails with `TransactionNotFound`, do not blindly rerun `scripts/deploy.ts`; that can send a second deployment transaction. First verify the transaction hash from the wallet or an independent read-only RPC/explorer check.

When the transaction is confirmed and `deployments/monad-testnet.json` is still absent, recover the artifact without any private key:

```bash
cd /home/holy/web3/focusproof-agent/contracts/monad-learning-task
export MONAD_RPC_URL=https://testnet-rpc.monad.xyz/
export MONAD_DEPLOYMENT_TX=<successful-deployment-transaction-hash>
export MONAD_DEPLOYMENT_OUTPUT=deployments/monad-testnet.json
npx hardhat run scripts/recover-deployment.ts
unset MONAD_RPC_URL MONAD_DEPLOYMENT_TX MONAD_DEPLOYMENT_OUTPUT
```

The recovery path is read-only. It creates a public artifact only when all checks pass: chain ID is `10143`, receipt status is successful, `to` is `null`, `contractAddress` is present, and `eth_getCode` at that address is non-empty. ABI, compiler version, and source commit are read from the current checkout; the same public artifact whitelist is enforced by `writeVerifiedDeploymentArtifact`.

## Recovered public deployment evidence

The following public Monad Testnet deployment was recovered on 2026-08-09 from read-only RPC evidence:

- Chain ID: `10143` (`0x279f`)
- Deployment transaction: `0x372553297ebca997d29e2171e0a56fd8f34d886b180ee830c50cfed68d682ed7`
- Receipt status: `0x1`
- Contract address: `0xa2f67973ce679a361b7bade60e664b5a3a44b470`
- Deployment block: `52001268`
- From: `0x455d8120a0a5efbc6160a0947ab4cda07e968fdd`
- `eth_getCode` prefix: `0x6080604052348015`
- `eth_getCode` hex digits excluding `0x`: `720`
- Artifact: `contracts/monad-learning-task/deployments/monad-testnet.json`

## Post-deployment verification before plugin enablement

Do not enable the plugin until an independent read-only check confirms all of the following:

1. Artifact `chainId` is `10143`.
2. The deployment transaction receipt succeeded and its contract address matches the artifact.
3. `eth_getCode` at the contract address is non-empty.
4. The deployment block is recorded from the successful receipt.
5. The ABI and `sourceCommit` match the deployed checkout.
6. A public explorer transaction link is constructed from an official current Monad Testnet explorer base URL verified on the deployment date.

## Enable the backend plugin

Set only configuration values in the backend process environment. Keep the RPC URL backend-only; it must never be exposed through frontend variables or browser bundles.

```bash
cd /home/holy/web3/focusproof-agent
export FOCUSPROOF_PLUGIN_MONAD_ENABLED=true
export FOCUSPROOF_MONAD_RPC_URL=<backend-only-https-rpc-url>
export FOCUSPROOF_MONAD_CHAIN_ID=10143
export FOCUSPROOF_MONAD_CONTRACT_ADDRESS=0xa2F67973ce679a361b7bAdE60e664b5A3A44B470
export FOCUSPROOF_MONAD_DEPLOYMENT_BLOCK=52001268
export FOCUSPROOF_MONAD_EXPLORER_TX_BASE_URL=<official-public-explorer-transaction-base-url>
.venv/bin/uvicorn focusproof.api.app:app --app-dir agent-server --host 127.0.0.1 --port 8000 --workers 1
```

The contract address must be EIP-55 checksummed before production-like enablement. Startup fails closed when enabled configuration is absent or invalid.

Start the frontend separately; it needs only the backend URL:

```bash
cd /home/holy/web3/focusproof-agent/frontend
FOCUSPROOF_API_BASE_URL=http://127.0.0.1:8000 npm run dev -- --hostname 127.0.0.1 --port 3000
```

After the acceptance run, unset all plugin configuration variables in that terminal. The user confirms `increment()` in an injected wallet; neither the frontend nor backend accepts a private key. Sender matching proves only the transaction sender and is not FocusProof identity proof.
