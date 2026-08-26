import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import hre from "hardhat";
import { createPublicClient, http, type Address, type Hex } from "viem";

import {
  MONAD_TESTNET_CHAIN_ID,
  writeVerifiedDeploymentArtifact,
} from "./deployment-artifact.js";

export type RecoveryDeploymentMetadata = {
  compilerVersion: string;
  sourceCommit: string;
  abi: readonly unknown[];
};

export type RecoveryTransactionReceipt = {
  status: "success" | "reverted";
  to: string | null;
  from: string;
  contractAddress: string | null | undefined;
  blockNumber: bigint;
};

export type RecoveryPublicClient = {
  getChainId(): Promise<number>;
  getTransactionReceipt(args: { hash: string }): Promise<RecoveryTransactionReceipt>;
  getCode(args: { address: string }): Promise<string | undefined>;
};

export type RecoverDeploymentOptions = {
  outputPath: string;
  transactionHash: string;
  publicClient: RecoveryPublicClient;
  deploymentMetadata: RecoveryDeploymentMetadata;
};

export function shouldRunRecoveryEntrypoint(
  currentModuleUrl: string,
  argv1: string | undefined,
  env: { MONAD_DEPLOYMENT_TX?: string },
): boolean {
  if (env.MONAD_DEPLOYMENT_TX !== undefined) {
    return true;
  }
  if (argv1 === undefined) {
    return false;
  }
  return currentModuleUrl === pathToFileURL(resolve(argv1)).href;
}

export async function recoverVerifiedDeploymentArtifact({
  outputPath,
  transactionHash,
  publicClient,
  deploymentMetadata,
}: RecoverDeploymentOptions): Promise<void> {
  const chainId = await publicClient.getChainId();
  if (chainId !== MONAD_TESTNET_CHAIN_ID) {
    throw new Error("unexpected Monad Testnet chain ID");
  }

  const receipt = await publicClient.getTransactionReceipt({
    hash: transactionHash,
  });
  if (receipt.status !== "success") {
    throw new Error("deployment transaction must have succeeded");
  }
  if (receipt.to !== null) {
    throw new Error("deployment transaction must be contract creation");
  }
  if (receipt.contractAddress == null) {
    throw new Error("deployment receipt missing contract address");
  }

  const deployedBytecode = await publicClient.getCode({
    address: receipt.contractAddress,
  });
  await writeVerifiedDeploymentArtifact(
    outputPath,
    {
      contractAddress: receipt.contractAddress,
      deploymentTransactionHash: transactionHash,
      chainId,
      compilerVersion: deploymentMetadata.compilerVersion,
      sourceCommit: deploymentMetadata.sourceCommit,
      abi: deploymentMetadata.abi,
    },
    deployedBytecode ?? "0x",
  );
}

async function readCheckoutDeploymentMetadata(): Promise<RecoveryDeploymentMetadata> {
  const artifact = await hre.artifacts.readArtifact("MonadLearningCounter");
  const sourceCommit = execFileSync("git", ["rev-parse", "HEAD"], {
    encoding: "utf8",
  }).trim();
  return {
    compilerVersion: "0.8.24",
    sourceCommit,
    abi: artifact.abi,
  };
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.trim() === "") {
    throw new Error(`${name} is required`);
  }
  return value;
}

function requireTransactionHash(value: string): Hex {
  if (!/^0x[0-9a-fA-F]{64}$/.test(value)) {
    throw new Error("MONAD_DEPLOYMENT_TX must be a 32-byte transaction hash");
  }
  return value as Hex;
}

async function main(): Promise<void> {
  const rpcUrl = requireEnv("MONAD_RPC_URL");
  const transactionHash = requireTransactionHash(requireEnv("MONAD_DEPLOYMENT_TX"));
  const outputPath = resolve(
    process.env.MONAD_DEPLOYMENT_OUTPUT ?? "deployments/monad-testnet.json",
  );
  const viemClient = createPublicClient({
    chain: {
      id: MONAD_TESTNET_CHAIN_ID,
      name: "Monad Testnet",
      nativeCurrency: { name: "MON", symbol: "MON", decimals: 18 },
      rpcUrls: { default: { http: [rpcUrl] } },
    },
    transport: http(rpcUrl),
  });

  await recoverVerifiedDeploymentArtifact({
    outputPath,
    transactionHash,
    publicClient: {
      getChainId: () => viemClient.getChainId(),
      getTransactionReceipt: ({ hash }) =>
        viemClient.getTransactionReceipt({ hash: hash as Hex }),
      getCode: ({ address }) => viemClient.getCode({ address: address as Address }),
    },
    deploymentMetadata: await readCheckoutDeploymentMetadata(),
  });
  console.log("Deployment metadata recovered to " + outputPath);
}

if (shouldRunRecoveryEntrypoint(import.meta.url, process.argv[1], process.env)) {
  await main();
}
