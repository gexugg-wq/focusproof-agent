import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "test-results/ai4c-staging",
  timeout: 120000,
  workers: 1,
  expect: { timeout: 15000 },
  use: {
    trace: "retain-on-failure"
  },
  projects: [{ name: "chromium" }]
});
