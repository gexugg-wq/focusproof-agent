import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const frontendDir = __dirname;
const repositoryDir = path.resolve(frontendDir, "..");
const runId = process.env.FOCUSPROOF_E2E_RUN_ID ?? "manual";
const runtimeDir = path.join(frontendDir, `test-results/ai4b-runtime-${runId}`);
const databasePath = path.join(runtimeDir, "focusproof.sqlite3");
const pythonPath = path.join(repositoryDir, ".venv/bin/python3.12");

const apiPort = Number(process.env.FOCUSPROOF_E2E_API_PORT ?? "8000");
const webPort = Number(process.env.FOCUSPROOF_E2E_WEB_PORT ?? "3000");
const apiBaseUrl = `http://127.0.0.1:${apiPort}`;
const webBaseUrl = `http://127.0.0.1:${webPort}`;
const generalTestIgnore = [
  "ai4c-staging.spec.ts",
  "ai4c-production-readiness.spec.ts",
  "focusproof-flow.spec.ts"
];
const scenarioSelection = { testIgnore: generalTestIgnore };

export default defineConfig({
  testDir: "./e2e",
  ...scenarioSelection,
  outputDir: "test-results/artifacts",
  timeout: 30000,
  workers: 1,
  expect: { timeout: 5000 },
  use: {
    baseURL: webBaseUrl,
    trace: "retain-on-failure"
  },
  webServer: [
    {
      command: [
        pythonPath,
        "scripts/run_ai4b_test_server.py",
        "--host 127.0.0.1",
        `--port ${apiPort}`,
        `--database-url sqlite+pysqlite:///${databasePath}`,
        `--data-dir ${runtimeDir}`,
        "--scenario general-flow"
      ].join(" "),
      cwd: repositoryDir,
      env: {
        LITELLM_LOCAL_MODEL_COST_MAP: "true",
        FOCUSPROOF_MEDIA_ENABLED: "true",
        FOCUSPROOF_MEDIA_SCANNER_MODE: "fake-clean",
        FOCUSPROOF_CLAMD_DEFINITIONS_VERSION: "deterministic-test",
        FOCUSPROOF_CLAMD_DEFINITIONS_FRESH_AT: "2026-08-26T00:00:00+00:00"
      },
      url: `${apiBaseUrl}/health`,
      reuseExistingServer: false,
      timeout: 120000
    },
    {
      command: `npm run dev -- --hostname 127.0.0.1 --port ${webPort}`,
      cwd: frontendDir,
      env: {
        FOCUSPROOF_API_BASE_URL: apiBaseUrl
      },
      url: webBaseUrl,
      reuseExistingServer: false,
      timeout: 120000
    }
  ],
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "desktop-1280", testIgnore: [...generalTestIgnore, "general-complete-flow.spec.ts"], use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } },
    { name: "mobile", testIgnore: [...generalTestIgnore, "general-complete-flow.spec.ts"], use: { ...devices["Pixel 5"], viewport: { width: 390, height: 844 } } },
    { name: "mobile-360", testIgnore: [...generalTestIgnore, "general-complete-flow.spec.ts"], use: { ...devices["Pixel 5"], viewport: { width: 360, height: 800 } } }
  ]
});
