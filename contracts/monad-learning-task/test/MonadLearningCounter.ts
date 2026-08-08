import assert from "node:assert/strict";
import { describe, it } from "node:test";

import hre from "hardhat";
import { encodeFunctionData } from "viem";

const { viem } = await hre.network.create();

describe("MonadLearningCounter", () => {
  it("keeps separate counters for each caller across 0 -> 1 -> 2", async () => {
    const [first, second] = await viem.getWalletClients();
    const counter = await viem.deployContract("MonadLearningCounter");
    const firstCounter = await viem.getContractAt(
      "MonadLearningCounter",
      counter.address,
      { client: { wallet: first } },
    );
    const secondCounter = await viem.getContractAt(
      "MonadLearningCounter",
      counter.address,
      { client: { wallet: second } },
    );

    assert.equal(await counter.read.counts([first.account.address]), 0n);
    assert.equal(await counter.read.counts([second.account.address]), 0n);

    await firstCounter.write.increment();
    await firstCounter.write.increment();
    await secondCounter.write.increment();

    assert.equal(await counter.read.counts([first.account.address]), 2n);
    assert.equal(await counter.read.counts([second.account.address]), 1n);
  });

  it("emits the exact learner and state transition", async () => {
    const [learner] = await viem.getWalletClients();
    const counter = await viem.deployContract("MonadLearningCounter");
    const learnerCounter = await viem.getContractAt(
      "MonadLearningCounter",
      counter.address,
      { client: { wallet: learner } },
    );

    await viem.assertions.emitWithArgs(
      learnerCounter.write.increment(),
      counter,
      "Incremented",
      [learner.account.address, 0n, 1n],
    );
  });

  it("rejects native value sent to increment", async () => {
    const [learner] = await viem.getWalletClients();
    const counter = await viem.deployContract("MonadLearningCounter");
    const data = encodeFunctionData({
      abi: counter.abi,
      functionName: "increment",
    });

    await assert.rejects(
      learner.sendTransaction({
        to: counter.address,
        data,
        value: 1n,
      }),
    );
  });

  it("exposes no function that changes another wallet counter", async () => {
    const counter = await viem.deployContract("MonadLearningCounter");
    const functionNames = counter.abi
      .filter((item) => item.type === "function")
      .map((item) => item.name)
      .sort();

    assert.deepEqual(functionNames, ["counts", "increment"]);
  });
});
