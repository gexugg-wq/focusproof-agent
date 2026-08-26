import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const frontendDir = __dirname;
const repositoryDir = path.resolve(frontendDir, "..");
const runId = process.env.FOCUSPROOF_E2E_RUN_ID ?? "demo";
const runtimeDir = path.join(frontendDir, `test-results/demo-runtime-${runId}`);
const databasePath = path.join(runtimeDir, "focusproof.sqlite3");
const pythonPath = path.join(repositoryDir, ".venv/bin/python3.12");
const apiPort = Number(process.env.FOCUSPROOF_E2E_API_PORT ?? "8010");
const webPort = Number(process.env.FOCUSPROOF_E2E_WEB_PORT ?? "3010");
const apiBaseUrl = `http://127.0.0.1:${apiPort}`;
const webBaseUrl = `http://127.0.0.1:${webPort}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: ["demo-deterministic-review-loop.spec.ts"],
  outputDir: "test-results/demo-artifacts",
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
        "scripts/run_demo_deterministic_server.py",
        "--host 127.0.0.1",
        `--port ${apiPort}`,
        `--database-url sqlite+pysqlite:///${databasePath}`,
        `--data-dir ${runtimeDir}`
      ].join(" "),
      cwd: repositoryDir,
      env: {
        FOCUSPROOF_PROFILE: "demo-deterministic",
        FOCUSPROOF_MEDIA_ENABLED: "true",
        FOCUSPROOF_MEDIA_SCANNER_MODE: "fake-clean",
        FOCUSPROOF_CLAMD_DEFINITIONS_VERSION: "demo-deterministic-test",
        FOCUSPROOF_CLAMD_DEFINITIONS_FRESH_AT: "2026-08-26T00:00:00+00:00",
        LITELLM_LOCAL_MODEL_COST_MAP: "true"
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
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }
  ]
});
