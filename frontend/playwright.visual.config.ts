import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const frontendDir = __dirname;
const repositoryDir = path.resolve(frontendDir, "..");
const runtimeDir = path.join(frontendDir, "test-results/ai4b-visual-runtime");
const databasePath = path.join(runtimeDir, "focusproof.sqlite3");
const pythonPath = path.join(repositoryDir, ".venv/bin/python3.12");

export default defineConfig({
  testDir: "./e2e",
  testMatch: "ai4b-real-flow.spec.ts",
  outputDir: "test-results/ai4b-visual-artifacts",
  timeout: 30000,
  workers: 1,
  expect: { timeout: 5000 },
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure"
  },
  webServer: [
    {
      command: [
        pythonPath,
        "scripts/run_ai4b_test_server.py",
        "--host 127.0.0.1",
        "--port 8000",
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
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false,
      timeout: 120000
    },
    {
      command:
        "npm run build && ./node_modules/.bin/next start --hostname 127.0.0.1 --port 3000",
      cwd: frontendDir,
      env: {
        FOCUSPROOF_API_BASE_URL: "http://127.0.0.1:8000"
      },
      url: "http://127.0.0.1:3000",
      reuseExistingServer: false,
      timeout: 120000
    }
  ],
  projects: [
    {
      name: "chromium",
      metadata: { visualCapture: true },
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }
    },
    {
      name: "desktop-1280",
      metadata: { visualCapture: true },
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } }
    },
    {
      name: "mobile",
      metadata: { visualCapture: true },
      use: { ...devices["Pixel 5"], viewport: { width: 390, height: 844 } }
    },
    {
      name: "mobile-360",
      metadata: { visualCapture: true },
      use: { ...devices["Pixel 5"], viewport: { width: 360, height: 800 } }
    }
  ]
});
