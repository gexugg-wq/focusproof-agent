import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

import hre from "hardhat";

import {
  MONAD_TESTNET_CHAIN_ID,
  writeVerifiedDeploymentArtifact,
} from "./deployment-artifact.js";

async function main(): Promise<void> {
  const { viem, networkName } = await hre.network.create();
  if (networkName !== "monad") {
    throw new Error("deployment requires --network monad");
  }
  const publicClient = await viem.getPublicClient();
  const chainId = await publicClient.getChainId();
  if (chainId !== MONAD_TESTNET_CHAIN_ID) {
    throw new Error("deployment requires Monad Testnet chain ID 10143");
  }
  const { contract, deploymentTransaction } =
    await viem.sendDeploymentTransaction("MonadLearningCounter");
  const receipt = await publicClient.waitForTransactionReceipt({
    hash: deploymentTransaction.hash,
    confirmations: 1,
  });
  if (receipt.status !== "success") {
    throw new Error("deployment transaction failed");
  }
  if (
    receipt.contractAddress?.toLowerCase() !== contract.address.toLowerCase()
  ) {
    throw new Error("deployment receipt contract address mismatch");
  }
  const deployedBytecode = await publicClient.getCode({
    address: contract.address,
  });
  const artifact = await hre.artifacts.readArtifact(
    "MonadLearningCounter",
  );
  const sourceCommit = execFileSync(
    "git",
    ["rev-parse", "HEAD"],
    { encoding: "utf8" },
  ).trim();
  const outputPath = resolve(
    process.env.MONAD_DEPLOYMENT_OUTPUT ??
      "deployments/monad-testnet.json",
  );
  await writeVerifiedDeploymentArtifact(
    outputPath,
    {
      contractAddress: contract.address,
      deploymentTransactionHash: deploymentTransaction.hash,
      chainId,
      compilerVersion: "0.8.24",
      sourceCommit,
      abi: artifact.abi,
    },
    deployedBytecode ?? "0x",
  );
  console.log("Deployment metadata written to " + outputPath);
}

await main();
