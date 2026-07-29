import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const frontendDir = __dirname;
const repositoryDir = path.resolve(frontendDir, "..");
const runtimeDir = path.join(frontendDir, "test-results/ai4b-runtime");
const databasePath = path.join(runtimeDir, "focusproof.sqlite3");
const pythonPath = path.join(repositoryDir, ".venv/bin/python3.12");

export default defineConfig({
  testDir: "./e2e",
  testIgnore: "ai4c-staging.spec.ts",
  outputDir: "test-results/artifacts",
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
      env: { LITELLM_LOCAL_MODEL_COST_MAP: "true" },
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false,
      timeout: 120000
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3000",
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
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "desktop-1280", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } },
    { name: "mobile", use: { ...devices["Pixel 5"], viewport: { width: 390, height: 844 } } },
    { name: "mobile-360", use: { ...devices["Pixel 5"], viewport: { width: 360, height: 800 } } }
  ]
});
