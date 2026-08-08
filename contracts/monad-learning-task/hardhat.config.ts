import hardhatToolboxViem from "@nomicfoundation/hardhat-toolbox-viem";
import { configVariable, defineConfig } from "hardhat/config";

export default defineConfig({
  plugins: [hardhatToolboxViem],
  paths: {
    sources: "./src",
    tests: {
      nodejs: "./test",
    },
  },
  networks: {
    monad: {
      type: "http",
      chainType: "l1",
      url: configVariable("MONAD_RPC_URL"),
      accounts: [configVariable("MONAD_DEPLOYER_PRIVATE_KEY")],
      timeout: 15_000,
    },
  },
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
    },
  },
});
