import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";

import {
  recoverVerifiedDeploymentArtifact,
  shouldRunRecoveryEntrypoint,
  type RecoveryDeploymentMetadata,
  type RecoveryPublicClient,
} from "../scripts/recover-deployment.js";

const TX_HASH =
  "0x372553297ebca997d29e2171e0a56fd8f34d886b180ee830c50cfed68d682ed7";
const CONTRACT_ADDRESS = "0xa2f67973ce679a361b7bade60e664b5a3a44b470";
const CALLER_ADDRESS = "0x455d8120a0a5efbc6160a0947ab4cda07e968fdd";
const DEPLOYMENT_METADATA: RecoveryDeploymentMetadata = {
  compilerVersion: "0.8.24",
  sourceCommit: "9e7fc34",
  abi: [],
};

function makePublicClient(
  overrides: Partial<{
    chainId: number;
    status: "success" | "reverted";
    to: string | null;
    contractAddress: string | null;
    deployedBytecode: string | undefined;
  }> = {},
): RecoveryPublicClient {
  return {
    getChainId: async () => overrides.chainId ?? 10143,
    getTransactionReceipt: async () => ({
      status: overrides.status ?? "success",
      to: overrides.to ?? null,
      from: CALLER_ADDRESS,
      contractAddress: Object.prototype.hasOwnProperty.call(
        overrides,
        "contractAddress",
      )
        ? overrides.contractAddress ?? null
        : CONTRACT_ADDRESS,
      blockNumber: 52_001_268n,
    }),
    getCode: async ({ address }) => {
      assert.equal(address.toLowerCase(), CONTRACT_ADDRESS);
      return overrides.deployedBytecode ?? "0x6080604052348015";
    },
  };
}

describe("deployment recovery", () => {
  it("detects the Hardhat recovery entrypoint from explicit public inputs", () => {
    assert.equal(
      shouldRunRecoveryEntrypoint(
        "file:///repo/contracts/monad-learning-task/scripts/recover-deployment.ts",
        "/repo/contracts/monad-learning-task/node_modules/.bin/hardhat",
        { MONAD_DEPLOYMENT_TX: TX_HASH },
      ),
      true,
    );
    assert.equal(
      shouldRunRecoveryEntrypoint(
        "file:///repo/contracts/monad-learning-task/scripts/recover-deployment.ts",
        "/repo/contracts/monad-learning-task/node_modules/.bin/hardhat",
        {},
      ),
      false,
    );
  });

  it("writes the public artifact from an already-mined contract creation transaction", async () => {
    const directory = await mkdtemp(join(tmpdir(), "focusproof-recovery-"));
    const output = join(directory, "deployment.json");

    try {
      await recoverVerifiedDeploymentArtifact({
        outputPath: output,
        transactionHash: TX_HASH,
        publicClient: makePublicClient(),
        deploymentMetadata: DEPLOYMENT_METADATA,
      });
      const artifact = JSON.parse(await readFile(output, "utf8"));

      assert.deepEqual(Object.keys(artifact).sort(), [
        "abi",
        "chainId",
        "compilerVersion",
        "contractAddress",
        "deploymentTransactionHash",
        "sourceCommit",
      ]);
      assert.equal(artifact.contractAddress, CONTRACT_ADDRESS);
      assert.equal(artifact.deploymentTransactionHash, TX_HASH);
      assert.equal(artifact.chainId, 10143);
      assert.equal(artifact.compilerVersion, "0.8.24");
      assert.equal(artifact.sourceCommit, "9e7fc34");
      assert.doesNotMatch(JSON.stringify(artifact), /rpc|private|secret/i);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("refuses unverified recovery evidence without writing an artifact", async () => {
    const cases: Array<{
      name: string;
      client: RecoveryPublicClient;
      message: RegExp;
    }> = [
      {
        name: "wrong chain",
        client: makePublicClient({ chainId: 1 }),
        message: /chain ID/,
      },
      {
        name: "failed receipt",
        client: makePublicClient({ status: "reverted" }),
        message: /succeeded/,
      },
      {
        name: "ordinary transaction",
        client: makePublicClient({ to: CONTRACT_ADDRESS }),
        message: /contract creation/,
      },
      {
        name: "missing contract address",
        client: makePublicClient({ contractAddress: null }),
        message: /contract address/,
      },
      {
        name: "empty bytecode",
        client: makePublicClient({ deployedBytecode: "0x" }),
        message: /bytecode/,
      },
    ];

    for (const testCase of cases) {
      const directory = await mkdtemp(join(tmpdir(), "focusproof-recovery-"));
      const output = join(directory, "deployment.json");

      try {
        await assert.rejects(
          recoverVerifiedDeploymentArtifact({
            outputPath: output,
            transactionHash: TX_HASH,
            publicClient: testCase.client,
            deploymentMetadata: DEPLOYMENT_METADATA,
          }),
          testCase.message,
        );
        await assert.rejects(stat(output), { code: "ENOENT" });
      } finally {
        await rm(directory, { recursive: true, force: true });
      }
    }
  });
});
