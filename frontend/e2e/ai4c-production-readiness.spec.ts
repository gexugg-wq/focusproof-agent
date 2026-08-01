import AxeBuilder from "@axe-core/playwright";
import { execFileSync } from "node:child_process";
import { readFileSync, rmSync } from "node:fs";
import https from "node:https";
import { join } from "node:path";
import { chromium, expect, test, type BrowserContext, type Page, type TestInfo } from "@playwright/test";

type JsonRecord = Record<string, unknown>;
type ManagedUser = { id: string; username: string };

const createdUsers: ManagedUser[] = [];

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`missing staging E2E input ${name}`);
  return value;
}

function restartBackend(): void {
  execFileSync("docker", composeArgs("restart", "agent-server"), { stdio: "ignore" });
  execFileSync("docker", composeArgs("up", "-d", "--wait", "agent-server"), {
    stdio: "ignore"
  });
}

function composeArgs(...args: string[]): string[] {
  return [
    "compose", "--project-name", required("FOCUSPROOF_STAGING_COMPOSE_PROJECT"),
    "-f", required("FOCUSPROOF_STAGING_COMPOSE_FILE"), ...args
  ];
}

function issuerUrl(): URL {
  const issuer = new URL(required("NEXT_PUBLIC_OIDC_ISSUER"));
  if (issuer.protocol !== "https:" || issuer.hostname !== "127.0.0.1") {
    throw new Error("local acceptance issuer must be explicit loopback HTTPS");
  }
  return issuer;
}

function adminRequest(
  method: string,
  path: string,
  body: string | undefined,
  contentType: string,
  token?: string
): Promise<{ status: number; body: string }> {
  const issuer = issuerUrl();
  return new Promise((resolve, reject) => {
    const request = https.request({
      hostname: issuer.hostname,
      port: issuer.port,
      path,
      method,
      ca: readFileSync(required("FOCUSPROOF_STAGING_OIDC_CA_FILE")),
      headers: {
        "content-type": contentType,
        ...(body ? { "content-length": Buffer.byteLength(body) } : {}),
        ...(token ? { authorization: `Bearer ${token}` } : {})
      }
    }, (response) => {
      const chunks: Buffer[] = [];
      response.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
      response.on("end", () => resolve({
        status: response.statusCode ?? 0,
        body: Buffer.concat(chunks).toString("utf8")
      }));
    });
    request.on("error", reject);
    if (body) request.write(body);
    request.end();
  });
}

async function adminToken(): Promise<string> {
  const password = readFileSync(required("FOCUSPROOF_STAGING_OIDC_ADMIN_PASSWORD_FILE"), "utf8").trim();
  const body = new URLSearchParams({
    client_id: "admin-cli",
    grant_type: "password",
    username: "focusproof-staging-admin",
    password
  }).toString();
  const response = await adminRequest(
    "POST", "/realms/master/protocol/openid-connect/token", body,
    "application/x-www-form-urlencoded"
  );
  expect(response.status).toBe(200);
  const payload = JSON.parse(response.body) as JsonRecord;
  expect(typeof payload.access_token).toBe("string");
  return payload.access_token as string;
}

async function createManagedUser(username: string): Promise<ManagedUser> {
  const token = await adminToken();
  const password = required("FOCUSPROOF_STAGING_TEST_USER_PASSWORD");
  const create = await adminRequest(
    "POST", "/admin/realms/focusproof/users",
    JSON.stringify({
      username, enabled: true, emailVerified: true,
      email: `${username}@example.invalid`,
      firstName: "AI4C",
      lastName: "Fixture",
      credentials: [{ type: "password", value: password, temporary: false }]
    }), "application/json", token
  );
  expect(create.status).toBe(201);
  const lookup = await adminRequest(
    "GET", `/admin/realms/focusproof/users?username=${encodeURIComponent(username)}&exact=true`,
    undefined, "application/json", token
  );
  expect(lookup.status).toBe(200);
  const users = JSON.parse(lookup.body) as JsonRecord[];
  expect(users).toHaveLength(1);
  const user = { id: String(users[0].id), username };
  createdUsers.push(user);
  return user;
}

async function deleteManagedUsers(): Promise<void> {
  if (createdUsers.length === 0) return;
  const token = await adminToken();
  const failures: Error[] = [];
  for (const user of [...createdUsers]) {
    try {
      const response = await adminRequest(
        "DELETE", `/admin/realms/focusproof/users/${encodeURIComponent(user.id)}`,
        undefined, "application/json", token
      );
      expect([204, 404]).toContain(response.status);
      createdUsers.splice(createdUsers.indexOf(user), 1);
    } catch (error) {
      failures.push(error instanceof Error ? error : new Error(String(error)));
    }
  }
  if (failures.length > 0) {
    throw new AggregateError(failures, "failed to delete one or more temporary OIDC users");
  }
}

function setPrincipalActive(subject: string, active: boolean): void {
  const sql = [
    "UPDATE verified_principals SET active =",
    active ? "true" : "false",
    "WHERE issuer = :'issuer' AND subject = :'subject';"
  ].join(" ");
  execFileSync("docker", composeArgs(
    "exec", "-T", "postgres", "psql", "-X", "-v", "ON_ERROR_STOP=1",
    "-U", "focusproof", "-d", "focusproof",
    "-v", `issuer=${required("NEXT_PUBLIC_OIDC_ISSUER").replace(/\/$/, "")}`,
    "-v", `subject=${subject}`
  ), { input: `${sql}\n`, stdio: ["pipe", "pipe", "pipe"] });
}

async function collectCleanupFailure(
  failures: Error[],
  label: string,
  cleanup: () => void | Promise<void>
): Promise<void> {
  try {
    await cleanup();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    failures.push(new Error(`${label}: ${detail}`));
  }
}

async function trustedContext(testInfo: TestInfo, label: string): Promise<BrowserContext> {
  const profileHome = testInfo.outputPath(`trusted-${label}`);
  const nssDirectory = join(profileHome, ".pki", "nssdb");
  const nssDatabase = `sql:${nssDirectory}`;
  execFileSync("mkdir", ["-p", nssDirectory]);
  execFileSync("certutil", ["-N", "-d", nssDatabase, "--empty-password"], { stdio: "ignore" });
  execFileSync("certutil", [
    "-A", "-d", nssDatabase, "-n", "focusproof-ai4c4-ca", "-t", "C,,",
    "-i", required("FOCUSPROOF_STAGING_OIDC_CA_FILE")
  ], { stdio: "ignore" });
  const context = await chromium.launchPersistentContext(join(profileHome, "chromium"), {
    headless: true,
    viewport: test.info().project.use.viewport,
    env: {
      HOME: profileHome,
      LANG: process.env.LANG ?? "C.UTF-8",
      LC_ALL: process.env.LC_ALL ?? "C.UTF-8",
      NODE_ENV: "test",
      PATH: process.env.PATH ?? ""
    }
  });
  context.on("close", () => rmSync(profileHome, { recursive: true, force: true }));
  return context;
}

async function login(page: Page, username: string): Promise<void> {
  await page.goto(required("FOCUSPROOF_STAGING_BROWSER_URL"));
  await expect(page).toHaveURL(/\/protocol\/openid-connect\/auth/);
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(required("FOCUSPROOF_STAGING_TEST_USER_PASSWORD"));
  await page.locator("#kc-login").click();
  await expect(page.getByRole("heading", { name: /create learning verification session/i })).toBeVisible();
}

async function createSession(page: Page, topic: string): Promise<string> {
  await page.getByLabel("Learning domain").selectOption("general");
  await page.getByLabel("Learning topic").fill(topic);
  await page.getByLabel("This session goal").fill("Explain deterministic reconstruction from immutable learning evidence.");
  await page.getByLabel("Expected output").fill("A concise independently checkable explanation");
  await page.getByRole("button", { name: /start session/i }).click();
  await expect(page).toHaveURL(/\/sessions\/[^/]+$/);
  return page.url().split("/").at(-1)!;
}

async function expectNoOverflow(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  const dimensions = await page.locator("html").evaluate((node) => ({
    clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
  const panels = page.locator("main > aside, main > div");
  const boxes = await panels.evaluateAll((nodes) => nodes.map((node) => {
    const rect = node.getBoundingClientRect();
    return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
  }));
  for (const box of boxes) {
    expect(box.left).toBeGreaterThanOrEqual(-1);
    expect(box.right).toBeLessThanOrEqual(dimensions.clientWidth + 1);
  }
  for (let left = 0; left < boxes.length; left += 1) {
    for (let right = left + 1; right < boxes.length; right += 1) {
      const overlaps = boxes[left].left < boxes[right].right &&
        boxes[left].right > boxes[right].left &&
        boxes[left].top < boxes[right].bottom &&
        boxes[left].bottom > boxes[right].top;
      expect(overlaps).toBe(false);
    }
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

const ai4bViewports = [
  { width: 1440, height: 900 }, { width: 1280, height: 720 },
  { width: 390, height: 844 }, { width: 360, height: 800 }
];

test.setTimeout(180000);

test("authenticated production BFF acceptance covers recovery, authorization, and accessibility", async ({ request }, testInfo) => {
  const suffix = `${testInfo.project.name.replace(/[^a-z0-9]/gi, "-")}-${Date.now()}`;
  const ownerB = await createManagedUser(`owner-b-${suffix}`);
  const disabled = await createManagedUser(`disabled-${suffix}`);
  const contexts: BrowserContext[] = [];
  let disabledWasMapped = false;
  let primaryFailure: unknown;
  try {
    const ownerAContext = await trustedContext(testInfo, "owner-a");
    contexts.push(ownerAContext);
    const ownerAPage = await ownerAContext.newPage();
    await login(ownerAPage, "learner");
    const sessionId = await createSession(ownerAPage, `AI4C acceptance ${suffix}`);

    const textInput = ownerAPage.getByLabel("Learning notes, explanation, code, or error record");
    await textInput.focus();
    await expect(textInput).toBeFocused();
    await textInput.fill("Append-only event replay rebuilds state by applying immutable events in sequence, preserving the history needed to reproduce the current view.");
    await ownerAPage.getByRole("button", { name: /submit evidence/i }).press("Enter");
    await expect(ownerAPage.getByText("Evidence submitted.")).toBeVisible();
    await ownerAPage.getByRole("button", { name: /end learning/i }).click();
    const reviewStatus = ownerAPage.getByRole("status", { name: /review state/i });
    await expect(reviewStatus).toHaveText(/awaiting user/i);
    await ownerAPage.getByLabel(/answer for /i).fill("Replay starts empty and applies the same immutable sequence in order.");
    await ownerAPage.getByRole("button", { name: /submit answer/i }).click();
    restartBackend();
    await ownerAPage.getByRole("button", { name: /request review again/i }).click();
    await expect(reviewStatus).toHaveText(/completed/i);
    await ownerAPage.reload();
    await expect(reviewStatus).toHaveText(/completed/i);
    await expect(ownerAPage.getByText("LikelyLearning")).toBeVisible();

    const accessibility = await new AxeBuilder({ page: ownerAPage }).analyze();
    expect(accessibility.violations).toEqual([]);
    for (const viewport of ai4bViewports) {
      await ownerAPage.setViewportSize(viewport);
      await expectNoOverflow(ownerAPage);
    }
    await ownerAPage.setViewportSize({ width: 640, height: 360 });
    await expectNoOverflow(ownerAPage);
    await ownerAPage.setViewportSize({ width: 1280, height: 720 });
    await ownerAPage.goto(required("FOCUSPROOF_STAGING_BROWSER_URL"));
    await createSession(ownerAPage, `AI4C URL acceptance ${suffix}`);
    await ownerAPage.getByRole("tab", { name: "URL" }).click();
    await ownerAPage.getByLabel("Source URL").fill("https://example.invalid/ai4c/local-acceptance");
    await ownerAPage.getByLabel("Explanation of the linked content").fill("A local deterministic reference for reconstruction boundaries.");
    await ownerAPage.getByRole("button", { name: /submit evidence/i }).click();
    await expect(ownerAPage.getByText("Evidence submitted.")).toBeVisible();


    const invalid = await request.get(`${required("FOCUSPROOF_STAGING_BROWSER_URL")}/api/focusproof/sessions/${sessionId}`, {
      headers: { authorization: "Bearer invalid-local-acceptance-token" }
    });
    expect(invalid.status()).toBe(401);

    const ownerBContext = await trustedContext(testInfo, "owner-b");
    contexts.push(ownerBContext);
    const ownerBPage = await ownerBContext.newPage();
    await login(ownerBPage, ownerB.username);
    const crossOwnerResponse = ownerBPage.waitForResponse((response) =>
      response.request().method() === "GET" && response.url().endsWith(`/api/focusproof/sessions/${sessionId}`)
    );
    await ownerBPage.goto(`${required("FOCUSPROOF_STAGING_BROWSER_URL")}/sessions/${sessionId}`);
    expect((await crossOwnerResponse).status()).toBe(404);

    const disabledContext = await trustedContext(testInfo, "disabled");
    contexts.push(disabledContext);
    const disabledPage = await disabledContext.newPage();
    await login(disabledPage, disabled.username);
    const disabledSessionId = await createSession(disabledPage, `Disabled boundary ${suffix}`);
    disabledWasMapped = true;
    setPrincipalActive(disabled.id, false);
    const retained = disabledPage.getByLabel("Learning notes, explanation, code, or error record");
    await retained.fill("This input must survive the authorization failure.");
    const forbiddenResponse = disabledPage.waitForResponse((response) =>
      response.request().method() === "POST" && response.url().endsWith(`/api/focusproof/sessions/${disabledSessionId}/evidence`)
    );
    await disabledPage.getByRole("button", { name: /submit evidence/i }).click();
    await expect(retained).toHaveValue("This input must survive the authorization failure.");
    expect((await forbiddenResponse).status()).toBe(403);

    console.log("AI4C_BROWSER_ACCEPTANCE", JSON.stringify({
      project: testInfo.project.name,
      authenticatedTextAndUrl: true,
      awaitingAndCompleted: true,
      refreshRecovery: true,
      authorizationStatuses: [401, 403, 404],
      axeViolations: 0,
      zoomPercent: 200
    }));
  } catch (error) {
    primaryFailure = error;
    throw error;
  } finally {
    const cleanupFailures: Error[] = [];
    if (disabledWasMapped) {
      await collectCleanupFailure(
        cleanupFailures,
        "restore temporary principal active state",
        () => setPrincipalActive(disabled.id, true)
      );
    }
    for (const context of contexts.reverse()) {
      await collectCleanupFailure(
        cleanupFailures,
        "close temporary browser context",
        () => context.close()
      );
    }
    await collectCleanupFailure(cleanupFailures, "delete temporary OIDC users", deleteManagedUsers);
    if (cleanupFailures.length > 0) {
      if (primaryFailure === undefined) {
        throw new AggregateError(cleanupFailures, "AI4C acceptance cleanup failed");
      }
      console.error(
        "AI4C_CLEANUP_AFTER_PRIMARY_FAILURE",
        cleanupFailures.map((failure) => failure.message)
      );
    }
  }
});
