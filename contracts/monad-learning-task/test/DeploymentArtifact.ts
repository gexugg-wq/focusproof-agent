import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";

import { writeVerifiedDeploymentArtifact } from "../scripts/deployment-artifact.js";

const PUBLIC_DEPLOYMENT = {
  contractAddress: "0x1111111111111111111111111111111111111111",
  deploymentTransactionHash:
    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  chainId: 10143,
  compilerVersion: "0.8.24",
  sourceCommit: "0123456789abcdef",
  abi: [],
};

describe("deployment artifact", () => {
  it("writes nothing until deployed bytecode is independently verified", async () => {
    const directory = await mkdtemp(join(tmpdir(), "focusproof-deployment-"));
    const output = join(directory, "deployment.json");

    try {
      await assert.rejects(
        writeVerifiedDeploymentArtifact(output, PUBLIC_DEPLOYMENT, "0x"),
        /bytecode/,
      );
      await assert.rejects(
        writeVerifiedDeploymentArtifact(
          output,
          { ...PUBLIC_DEPLOYMENT, chainId: 1 },
          PUBLIC_DEPLOYMENT.deploymentTransactionHash.slice(0, 6),
        ),
        /chain ID/,
      );
      await assert.rejects(stat(output), { code: "ENOENT" });
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("writes only public deployment metadata after verification", async () => {
    const directory = await mkdtemp(join(tmpdir(), "focusproof-deployment-"));
    const output = join(directory, "deployment.json");

    try {
      await writeVerifiedDeploymentArtifact(
        output,
        {
          ...PUBLIC_DEPLOYMENT,
          rpcUrl: "https://secret-rpc.example",
          deployerPrivateKey: "0xprivate",
        },
        "0x6000",
      );
      const artifact = JSON.parse(await readFile(output, "utf8"));

      assert.deepEqual(Object.keys(artifact).sort(), [
        "abi",
        "chainId",
        "compilerVersion",
        "contractAddress",
        "deploymentTransactionHash",
        "sourceCommit",
      ]);
      assert.doesNotMatch(JSON.stringify(artifact), /secret-rpc|private/);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
