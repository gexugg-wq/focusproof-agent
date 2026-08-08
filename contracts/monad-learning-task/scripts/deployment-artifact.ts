import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

export type PublicDeployment = {
  contractAddress: string;
  deploymentTransactionHash: string;
  chainId: number;
  compilerVersion: string;
  sourceCommit: string;
  abi: readonly unknown[];
  [key: string]: unknown;
};

export async function writeVerifiedDeploymentArtifact(
  outputPath: string,
  deployment: PublicDeployment,
  deployedBytecode: string,
): Promise<void> {
  if (deployedBytecode === "0x" || deployedBytecode === "0x0") {
    throw new Error("deployed bytecode verification failed");
  }
  const publicArtifact = {
    contractAddress: deployment.contractAddress,
    deploymentTransactionHash: deployment.deploymentTransactionHash,
    chainId: deployment.chainId,
    compilerVersion: deployment.compilerVersion,
    sourceCommit: deployment.sourceCommit,
    abi: deployment.abi,
  };
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(
    outputPath,
    JSON.stringify(publicArtifact, null, 2) + "\n",
    { encoding: "utf8", flag: "wx" },
  );
}
