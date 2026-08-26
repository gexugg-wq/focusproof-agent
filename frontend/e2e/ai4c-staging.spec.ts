import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import {
  chromium,
  expect,
  test,
  type BrowserContext,
  type Page,
  type Response,
  type TestInfo
} from "@playwright/test";

type ApiPayload = Record<string, unknown>;
type ProjectedEvent = {
  id: string;
  sequence: number;
  type: string;
  payload: Record<string, unknown>;
};

type ReviewProjection = {
  reviewId: string;
  sessionId: string;
  conversationId: string;
  reviewStatus: string;
  nativeEventCount: number;
  sourceOpenHandsEventId: string | null;
};

type BffResponse = {
  authorizationPresent: boolean;
  audienceMatches: boolean;
  issuerMatches: boolean;
  method: string;
  notBeforeValidWhenPresent: boolean;
  requiredClaimsPresent: boolean;
  status: number;
  url: string;
  body: ApiPayload;
};

function tokenFacts(authorization: string | undefined): Pick<
  BffResponse,
  | "authorizationPresent"
  | "audienceMatches"
  | "issuerMatches"
  | "notBeforeValidWhenPresent"
  | "requiredClaimsPresent"
> {
  if (!authorization?.startsWith("Bearer ")) {
    return {
      authorizationPresent: false,
      audienceMatches: false,
      issuerMatches: false,
      notBeforeValidWhenPresent: false,
      requiredClaimsPresent: false
    };
  }
  try {
    const [, encodedClaims] = authorization.slice("Bearer ".length).split(".");
    const claims = JSON.parse(Buffer.from(encodedClaims, "base64url").toString("utf8")) as Record<string, unknown>;
    const audience = claims.aud;
    return {
      authorizationPresent: true,
      audienceMatches: Array.isArray(audience)
        ? audience.includes(required("NEXT_PUBLIC_OIDC_AUDIENCE"))
        : audience === required("NEXT_PUBLIC_OIDC_AUDIENCE"),
      issuerMatches: claims.iss === required("NEXT_PUBLIC_OIDC_ISSUER"),
      notBeforeValidWhenPresent:
        typeof claims.nbf !== "number" || claims.nbf <= Math.floor(Date.now() / 1000),
      requiredClaimsPresent: ["sub", "exp", "iat"].every(
        (name) => typeof claims[name] === "string" || typeof claims[name] === "number"
      )
    };
  } catch {
    return {
      authorizationPresent: false,
      audienceMatches: false,
      issuerMatches: false,
      notBeforeValidWhenPresent: false,
      requiredClaimsPresent: false
    };
  }
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`missing staging E2E input ${name}`);
  return value;
}

function requiredStagingBrowserUrl(): string {
  const browserUrl = required("FOCUSPROOF_STAGING_BROWSER_URL");
  const parsed = new URL(browserUrl);
  if (parsed.protocol !== "http:" || parsed.hostname !== "127.0.0.1") {
    throw new Error("FOCUSPROOF_STAGING_BROWSER_URL must be an explicit http://127.0.0.1 URL");
  }
  return parsed.toString();
}

function requiredHttpsIssuer(): string {
  const issuer = required("NEXT_PUBLIC_OIDC_ISSUER").replace(/\/$/, "");
  const parsed = new URL(issuer);
  if (parsed.protocol !== "https:" || parsed.hostname !== "127.0.0.1") {
    throw new Error("NEXT_PUBLIC_OIDC_ISSUER must be an explicit https://127.0.0.1 issuer");
  }
  return issuer;
}

function isolatedBrowserEnvironment(profileHome: string): NodeJS.ProcessEnv {
  const nodeEnvironment = process.env.NODE_ENV;
  if (
    nodeEnvironment !== "development" &&
    nodeEnvironment !== "production" &&
    nodeEnvironment !== "test"
  ) {
    throw new Error("NODE_ENV must be provided by the staging E2E host");
  }
  return {
    HOME: profileHome,
    LANG: process.env.LANG ?? "C.UTF-8",
    LC_ALL: process.env.LC_ALL ?? "C.UTF-8",
    NODE_ENV: nodeEnvironment,
    PATH: process.env.PATH ?? ""
  };
}

async function launchTrustedStagingChromium(
  testInfo: TestInfo
): Promise<{ context: BrowserContext; profileHome: string }> {
  const profileHome = testInfo.outputPath("trusted-browser-home");
  const nssDirectory = join(profileHome, ".pki", "nssdb");
  const nssDatabase = `sql:${nssDirectory}`;
  const environment = isolatedBrowserEnvironment(profileHome);
  try {
    mkdirSync(nssDirectory, { recursive: true });
    execFileSync("certutil", ["-N", "-d", nssDatabase, "--empty-password"], {
      env: environment,
      stdio: "ignore"
    });
    execFileSync(
      "certutil",
      [
        "-A",
        "-d",
        nssDatabase,
        "-n",
        "focusproof-staging-oidc-ca",
        "-t",
        "C,,",
        "-i",
        required("FOCUSPROOF_STAGING_OIDC_CA_FILE")
      ],
      { env: environment, stdio: "ignore" }
    );
    const context = await chromium.launchPersistentContext(join(profileHome, "chromium-profile"), {
      env: environment,
      headless: true,
      viewport: { width: 1280, height: 720 }
    });
    return { context, profileHome };
  } catch (error) {
    rmSync(profileHome, { force: true, recursive: true });
    throw error;
  }
}

function keycloakAuthorizationUrl(issuer: string): RegExp {
  return new RegExp(
    `^${issuer.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/protocol/openid-connect/auth`
  );
}

async function payload(response: Response): Promise<ApiPayload | null> {
  if (!response.url().includes("/api/focusproof/")) return null;
  const contentType = response.headers()["content-type"] ?? "";
  if (!contentType.includes("application/json")) return null;
  return response.json() as Promise<ApiPayload>;
}

function recordField(body: ApiPayload, name: string): ApiPayload {
  const value = body[name];
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw new Error(`expected ${name} object in BFF response`);
  }
  return value as ApiPayload;
}

function stringField(body: ApiPayload, name: string): string {
  const value = body[name];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`expected ${name} string in BFF response`);
  }
  return value;
}

function sourceOpenHandsEventIds(events: ProjectedEvent[]): string[] {
  return events.flatMap((event) => {
    const sourceId = event.payload.sourceOpenHandsEventId;
    return typeof sourceId === "string" ? [sourceId] : [];
  });
}

function expectAuthenticatedBff(response: BffResponse): void {
  expect(response.authorizationPresent).toBe(true);
  expect(response.issuerMatches).toBe(true);
  expect(response.audienceMatches).toBe(true);
  expect(response.requiredClaimsPresent).toBe(true);
  expect(response.notBeforeValidWhenPresent).toBe(true);
  expect(response.status, JSON.stringify(response.body)).toBe(200);
}

async function login(page: Page, browserUrl: string, issuer: string): Promise<void> {
  await page.goto(browserUrl);
  await expect(page).toHaveURL(keycloakAuthorizationUrl(issuer));
  await page.locator("#username").fill("learner");
  await page.locator("#password").fill(required("FOCUSPROOF_STAGING_TEST_USER_PASSWORD"));
  await page.locator("#kc-login").click();
  await expect(page.getByRole("heading", { name: /create learning verification session/i })).toBeVisible();
}

function restartBackend(): void {
  const common = [
    "compose",
    "--project-name",
    required("FOCUSPROOF_STAGING_COMPOSE_PROJECT"),
    "-f",
    required("FOCUSPROOF_STAGING_COMPOSE_FILE")
  ];
  execFileSync("docker", [...common, "restart", "agent-server"], { stdio: "ignore" });
  execFileSync("docker", [...common, "up", "-d", "--wait", "agent-server"], {
    stdio: "ignore"
  });
}

test("browser OIDC code PKCE continues a native Conversation after backend restart", async ({}, testInfo) => {
  const browserUrl = requiredStagingBrowserUrl();
  const issuer = requiredHttpsIssuer();
  const { context, profileHome } = await launchTrustedStagingChromium(testInfo);
  const page = await context.newPage();
  try {
  const reviewResponses: ApiPayload[] = [];
  const bffResponses: BffResponse[] = [];
  const sessionResponses: BffResponse[] = [];
  const eventResponses: BffResponse[] = [];
  const reviewListResponses: BffResponse[] = [];
  let latestEvents: ProjectedEvent[] = [];
  let latestReviews: ReviewProjection[] = [];
  page.on("response", (response) => {
    void payload(response).then((body) => {
      if (!body) return;
      const url = response.url();
      const bffResponse = {
        ...tokenFacts(response.request().headers().authorization),
        method: response.request().method(),
        status: response.status(),
        url,
        body
      };
      bffResponses.push(bffResponse);
      if (response.request().method() === "POST" && url.endsWith("/review")) {
        reviewResponses.push(body);
      }
      if (response.request().method() === "GET" && /\/sessions\/[^/]+$/.test(url)) {
        sessionResponses.push(bffResponse);
      }
      if (response.request().method() === "GET" && url.endsWith("/events")) {
        const events = body.events;
        if (Array.isArray(events)) {
          latestEvents = events as ProjectedEvent[];
          eventResponses.push(bffResponse);
        }
      }
      if (response.request().method() === "GET" && url.endsWith("/reviews")) {
        const reviews = body.reviews;
        if (Array.isArray(reviews)) {
          latestReviews = reviews as ReviewProjection[];
          reviewListResponses.push(bffResponse);
        }
      }
    });
  });

  await login(page, browserUrl, issuer);
  await page.getByLabel("Learning domain").selectOption("general");
  await page.getByLabel("Learning topic").fill("Native recovery continuity");
  await page
    .getByLabel("This session goal")
    .fill("Explain how native OpenHands events continue after a backend restart.");
  await page.getByLabel("Expected output").fill("A concise continuity explanation");
  await page.getByRole("button", { name: /start session/i }).click();
  await expect.poll(() => bffResponses.filter((response) => response.method === "POST" && response.url.endsWith("/sessions")).length).toBeGreaterThan(0);
  const createResponse = bffResponses.filter(
    (response) => response.method === "POST" && response.url.endsWith("/sessions")
  ).at(-1)!;
  expectAuthenticatedBff(createResponse);
  await expect(page).toHaveURL(/\/sessions\/[^/]+$/);

  await page
    .getByLabel("Learning evidence")
    .fill("The native EventLog owns ordered runtime facts while product events are projections.");
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText("Evidence submitted.")).toBeVisible();
  await page.getByRole("button", { name: /end learning/i }).click();
  const reviewState = page.getByRole("status", { name: /review state/i });
  await expect(reviewState).toHaveText(/awaiting user/i);
  await expect.poll(() => reviewResponses.length).toBeGreaterThan(0);
  await expect.poll(() => latestEvents.length).toBeGreaterThan(0);
  await expect.poll(() => sessionResponses.length).toBeGreaterThan(0);
  await expect.poll(() => eventResponses.length).toBeGreaterThan(0);

  const beforeReview = reviewResponses.at(-1)!;
  expect(beforeReview.usedOpenHandsConversation).toBe(true);
  expect(beforeReview.reviewStatus).toBe("awaiting_user");
  const preRestartSessionResponse = sessionResponses.at(-1)!;
  expectAuthenticatedBff(preRestartSessionResponse);
  const conversationId = stringField(recordField(preRestartSessionResponse.body, "state"), "conversationId");
  expect(beforeReview.conversationId).toBe(conversationId);
  const nativeCountBefore = Number(beforeReview.nativeEventCount);
  const beforeEvents = [...latestEvents];
  const beforeProductEventIds = beforeEvents.map((event) => event.id);
  const beforeSourceOpenHandsEventIds = sourceOpenHandsEventIds(beforeEvents);
  expect(beforeProductEventIds.length).toBeGreaterThan(0);
  expect(beforeSourceOpenHandsEventIds.length).toBeGreaterThan(0);

  const sessionGetsBeforeRecovery = sessionResponses.length;
  const eventGetsBeforeRecovery = eventResponses.length;
  const reviewGetsBeforeRecovery = reviewListResponses.length;
  restartBackend();

  await page
    .getByLabel(/answer for /i)
    .fill("Native event identity and ordering remain durable, so restoration can append rather than replay a projection as runtime truth.");
  await page.getByRole("button", { name: /submit answer/i }).click();
  await expect(page.getByText("Answer submitted.")).toBeVisible();
  await expect.poll(() => bffResponses.filter(
    (response) => response.method === "POST" && response.url.endsWith("/answer")
  ).length).toBeGreaterThan(0);
  expectAuthenticatedBff(bffResponses.filter(
    (response) => response.method === "POST" && response.url.endsWith("/answer")
  ).at(-1)!);
  await page.getByRole("button", { name: /request review again/i }).click();
  await expect(reviewState).toHaveText(/awaiting user|completed/i);
  if ((await reviewState.textContent())?.toLowerCase().includes("awaiting")) {
    await page.getByRole("button", { name: /request review again/i }).click();
  }
  await expect(reviewState).toHaveText(/completed/i);
  await expect.poll(() => reviewResponses.at(-1)?.reviewStatus).toBe("completed");
  await expect.poll(() => latestEvents.some((event) => event.type === "review.completed")).toBe(true);
  await expect.poll(() => sessionResponses.length).toBeGreaterThan(sessionGetsBeforeRecovery);
  await expect.poll(() => eventResponses.length).toBeGreaterThan(eventGetsBeforeRecovery);
  await expect.poll(() => reviewListResponses.length).toBeGreaterThan(reviewGetsBeforeRecovery);

  const afterReview = reviewResponses.at(-1)!;
  expectAuthenticatedBff(bffResponses.filter(
    (response) => response.method === "POST" && response.url.endsWith("/review")
  ).at(-1)!);
  expect(afterReview.usedOpenHandsConversation).toBe(true);
  expect(afterReview.conversationId).toBe(conversationId);
  expect(Number(afterReview.nativeEventCount)).toBeGreaterThan(nativeCountBefore);

  const postRecoverySessionResponse = sessionResponses.at(-1)!;
  expectAuthenticatedBff(postRecoverySessionResponse);
  expect(stringField(recordField(postRecoverySessionResponse.body, "state"), "conversationId"))
    .toBe(conversationId);

  const postRecoveryEvents = [...latestEvents];
  const postRecoveryProductEventIds = postRecoveryEvents.map((event) => event.id);
  const postRecoverySourceOpenHandsEventIds = sourceOpenHandsEventIds(postRecoveryEvents);
  expect(postRecoveryProductEventIds.slice(0, beforeProductEventIds.length))
    .toEqual(beforeProductEventIds);
  expect(postRecoverySourceOpenHandsEventIds.slice(0, beforeSourceOpenHandsEventIds.length))
    .toEqual(beforeSourceOpenHandsEventIds);
  expect(new Set(postRecoverySourceOpenHandsEventIds).size).toBe(postRecoverySourceOpenHandsEventIds.length);
  const indexes = postRecoveryEvents
    .filter((event) => typeof event.payload.sourceOpenHandsEventId === "string")
    .map((event) => Number(event.payload.sourceOpenHandsEventIndex));
  expect(indexes).toEqual([...indexes].sort((left, right) => left - right));
  expect(indexes.slice(beforeSourceOpenHandsEventIds.length).every((index) => index > indexes[beforeSourceOpenHandsEventIds.length - 1]))
    .toBe(true);

  const completedReview = latestReviews.find(
    (review) => review.reviewStatus === "completed" && review.conversationId === conversationId
  );
  expect(completedReview).toBeDefined();
  const reviewId = completedReview!.reviewId;
  expect(reviewId).toBeTruthy();

  const sessionGetsBeforeSecondRestart = sessionResponses.length;
  const eventGetsBeforeSecondRestart = eventResponses.length;
  const reviewGetsBeforeSecondRestart = reviewListResponses.length;
  restartBackend();
  await page.reload();
  await expect(page.getByRole("heading", { name: /native recovery continuity/i })).toBeVisible();
  await expect.poll(() => sessionResponses.length).toBeGreaterThan(sessionGetsBeforeSecondRestart);
  await expect.poll(() => eventResponses.length).toBeGreaterThan(eventGetsBeforeSecondRestart);
  await expect.poll(() => reviewListResponses.length).toBeGreaterThan(reviewGetsBeforeSecondRestart);

  const secondRestartSessionResponse = sessionResponses.at(-1)!;
  const secondRestartEventResponse = eventResponses.at(-1)!;
  const secondRestartReviewResponse = reviewListResponses.at(-1)!;
  expectAuthenticatedBff(secondRestartSessionResponse);
  expectAuthenticatedBff(secondRestartEventResponse);
  expectAuthenticatedBff(secondRestartReviewResponse);
  expect(stringField(recordField(secondRestartSessionResponse.body, "state"), "conversationId"))
    .toBe(conversationId);

  const secondRestartProductEventIds = latestEvents.map((event) => event.id);
  const secondRestartSourceOpenHandsEventIds = sourceOpenHandsEventIds(latestEvents);
  expect(secondRestartProductEventIds.length).toBeGreaterThanOrEqual(postRecoveryProductEventIds.length);
  expect(secondRestartProductEventIds.slice(0, postRecoveryProductEventIds.length))
    .toEqual(postRecoveryProductEventIds);
  expect(secondRestartSourceOpenHandsEventIds.length)
    .toBeGreaterThanOrEqual(postRecoverySourceOpenHandsEventIds.length);
  expect(secondRestartSourceOpenHandsEventIds.slice(0, postRecoverySourceOpenHandsEventIds.length))
    .toEqual(postRecoverySourceOpenHandsEventIds);
  expect(latestReviews.some((review) => review.reviewId === reviewId)).toBe(true);

  console.log(
    "AI4C_NATIVE_RECOVERY_EVIDENCE",
    JSON.stringify({
      conversationId,
      reviewId,
      preProductEventCount: beforeProductEventIds.length,
      postRecoveryProductEventCount: postRecoveryProductEventIds.length,
      secondRestartProductEventCount: secondRestartProductEventIds.length,
      preNativeSourceEventCount: beforeSourceOpenHandsEventIds.length,
      postRecoveryNativeSourceEventCount: postRecoverySourceOpenHandsEventIds.length,
      secondRestartNativeSourceEventCount: secondRestartSourceOpenHandsEventIds.length,
      reviewStatus: afterReview.reviewStatus
    })
  );
  } finally {
    await context.close();
    rmSync(profileHome, { force: true, recursive: true });
  }
});
