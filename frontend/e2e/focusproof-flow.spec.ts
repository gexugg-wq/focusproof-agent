import { expect, test, type Page, type Route } from "@playwright/test";

const sessionId = "sess_mock_ai3";
const assetsDir = "../docs/research/assets/ai3/";

const baseSession = {
  sessionId,
  state: {
    sessionId,
    ownerUserId: "dev-anonymous-user",
    status: "running",
    goal: {
      domain: "programming",
      title: "Understanding event sourcing",
      goal: "Explain why an EventLog is a fact source and a View is derived state.",
      expectedOutput: "review note",
      plannedMinutes: 25
    },
    evidence: [],
    answers: {},
    observations: [],
    previousActions: [],
    reviewResult: null,
    adapterMode: "openhands-local-real",
    conversationId: "conv_mock",
    runtimeMode: "openhands-local-real"
  },
  view: {}
};

const completedReview = {
  sessionId,
  conversationMode: "openhands-local-real",
  usedOpenHandsConversation: true,
  reviewStatus: "completed",
  reviewResult: {
    status: "LikelyLearning",
    score: 84,
    confidence: 0.78,
    dimensions: { evidence: 85, explanation: 82, correction: 80 },
    findings: [{ severity: "info", message: "The learner separated append-only facts from rebuildable projections.", evidenceIds: [], observationRefs: [] }],
    summary: "The evidence supports the event sourcing learning goal.",
    nextStep: "Compare replay behavior with a mutable CRUD update."
  }
};

function cloneSession(reviewResult: typeof completedReview.reviewResult | null = null) {
  return {
    ...baseSession,
    state: {
      ...baseSession.state,
      evidence: reviewResult ? [{ evidenceId: "ev_1", evidenceType: "text", contentHash: "sha256:mock", textContent: "EventLog stores immutable facts; View is rebuilt.", sourceUrl: null, metadata: {} }] : [],
      answers: reviewResult ? { q1: "Replay separates facts from views." } : {},
      reviewResult
    }
  };
}

async function mockApi(page: Page) {
  let reviewCount = 0;
  let sessionReview: typeof completedReview.reviewResult | null = null;
  await page.route("**/api/focusproof/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/health")) return route.fulfill({ json: { status: "ok" } });
    if (path.endsWith("/sessions") && route.request().method() === "POST") return route.fulfill({ json: { sessionId, status: "running" } });
    if (path.endsWith("/evidence")) return route.fulfill({ json: { sessionId, evidenceId: "ev_1", syncPending: true } });
    if (path.endsWith("/answer")) return route.fulfill({ json: { sessionId, questionId: "q1", syncPending: false } });
    if (path.endsWith("/review")) {
      reviewCount += 1;
      if (reviewCount === 1) {
        return route.fulfill({ json: { sessionId, conversationMode: "openhands-local-real", usedOpenHandsConversation: true, reviewStatus: "awaiting_user", agentQuestions: [{ questionId: "q1", question: "What makes the EventLog different from a View?" }] } });
      }
      sessionReview = completedReview.reviewResult;
      return route.fulfill({ json: completedReview });
    }
    if (path.endsWith("/events")) {
      return route.fulfill({ json: { events: [
        { id: "evt_3", sessionId, type: "review.completed", sequence: 3, createdAt: "2026-07-13T09:00:00Z", actor: "agent", payload: {} },
        { id: "evt_1", sessionId, type: "session.created", sequence: 1, createdAt: "2026-07-13T08:40:00Z", actor: "system", payload: {} },
        { id: "evt_2", sessionId, type: "evidence.submitted", sequence: 2, createdAt: "2026-07-13T08:50:00Z", actor: "user", payload: {} }
      ] } });
    }
    if (path.includes("/sessions/")) return route.fulfill({ json: cloneSession(sessionReview) });
    return route.fulfill({ status: 404, json: { code: "not_found" } });
  });
}

async function maybeScreenshot(page: Page, projectName: string, targetProject: string, fileName: string) {
  if (projectName === targetProject) {
    await page.screenshot({ path: assetsDir + fileName, fullPage: true });
  }
}

test("completes the general FocusProof review loop and refresh recovery", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.goto("/");
  await maybeScreenshot(page, testInfo.project.name, "chromium", "01-create-session-desktop.png");
  await page.getByLabel("Learning topic").fill("Understanding event sourcing");
  await page.getByLabel("This session goal").fill("Explain why an EventLog is a fact source and a View is derived state.");
  await page.getByRole("button", { name: /start 25 minutes/i }).click();
  await expect(page).toHaveURL(/sessions\/sess_mock_ai3/);
  await expect(page.getByText("Understanding event sourcing")).toBeVisible();
  await page.getByLabel("Learning evidence").fill("EventLog entries are immutable facts. A View is derived and can be rebuilt from replay.");
  await maybeScreenshot(page, testInfo.project.name, "chromium", "02-general-evidence-desktop.png");
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText(/waiting for Agent sync/i)).toBeVisible();
  await page.getByRole("button", { name: /end learning/i }).click();
  await expect(page.getByText(/EventLog different from a View/i)).toBeVisible();
  await maybeScreenshot(page, testInfo.project.name, "chromium", "03-awaiting-user-desktop.png");
  await maybeScreenshot(page, testInfo.project.name, "mobile", "07-awaiting-user-mobile.png");
  await page.getByLabel(/answer for q1/i).fill("The EventLog is the durable sequence of facts; the View is derived and disposable.");
  await page.getByRole("button", { name: /submit answer/i }).click();
  await page.getByRole("button", { name: /request review again/i }).click();
  await expect(page.getByText("84")).toBeVisible();
  await expect(page.getByText("LikelyLearning")).toBeVisible();
  await expect(page.getByText(/Confidence 78/i)).toBeVisible();
  await expect(page.getByText("evidence", { exact: true })).toBeVisible();
  await expect(page.getByText(/separated append-only facts/i)).toBeVisible();
  await expect(page.getByText(/supports the event sourcing learning goal/i)).toBeVisible();
  await expect(page.getByText(/Compare replay behavior/i)).toBeVisible();
  await expect(page.getByText(/not a judgment of the learner/i)).toBeVisible();
  await expect(page.getByText(/Session created/i)).toBeVisible();
  await maybeScreenshot(page, testInfo.project.name, "chromium", "04-review-completed-desktop.png");
  await maybeScreenshot(page, testInfo.project.name, "mobile", "08-review-completed-mobile.png");
  await page.reload();
  await expect(page.getByText("Understanding event sourcing")).toBeVisible();
  await expect(page.getByText("84")).toBeVisible();
  await expect(page.getByText(/supports the event sourcing learning goal/i)).toBeVisible();
});

test("shows precise retryable 409 and 503 review errors", async ({ page }, testInfo) => {
  await page.route("**/api/focusproof/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.includes("/sessions/") && !path.endsWith("/review") && !path.endsWith("/events")) return route.fulfill({ json: cloneSession(null) });
    if (path.endsWith("/events")) return route.fulfill({ json: { events: [] } });
    if (path.endsWith("/review")) return route.fulfill({ status: 409, json: { code: "session_busy", retryable: true } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/sessions/" + sessionId);
  await page.getByRole("button", { name: /end learning/i }).click();
  await expect(page.getByText("Session processing is still in progress. Please retry shortly.")).toBeVisible();
  await maybeScreenshot(page, testInfo.project.name, "chromium", "05-session-busy-desktop.png");

  await page.unroute("**/api/focusproof/**");
  await page.route("**/api/focusproof/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.includes("/sessions/") && !path.endsWith("/review") && !path.endsWith("/events")) return route.fulfill({ json: cloneSession(null) });
    if (path.endsWith("/events")) return route.fulfill({ json: { events: [] } });
    if (path.endsWith("/review")) return route.fulfill({ status: 503, json: { code: "backend_unavailable", retryable: true } });
    return route.fulfill({ json: {} });
  });
  await page.reload();
  await page.getByRole("button", { name: /end learning/i }).click();
  await expect(page.getByText("Agent Runtime current unavailable. Page data has been preserved.")).toBeVisible();
  await maybeScreenshot(page, testInfo.project.name, "chromium", "06-runtime-unavailable-desktop.png");
});

test("keeps Web3 evidence optional and isolated from the general flow", async ({ page }, testInfo) => {
  await page.route("**/api/focusproof/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const web3Session = {
      ...cloneSession(null),
      state: {
        ...cloneSession(null).state,
        goal: {
          ...cloneSession(null).state.goal,
          domain: "web3",
          title: "Local transaction receipt",
          goal: "Explain a local transaction receipt without writing proof on-chain."
        }
      }
    };
    if (path.endsWith("/evidence")) return route.fulfill({ json: { sessionId, evidenceId: "ev_web3", syncPending: true } });
    if (path.endsWith("/events")) return route.fulfill({ json: { events: [] } });
    if (path.includes("/sessions/")) return route.fulfill({ json: web3Session });
    return route.fulfill({ json: {} });
  });
  await page.goto("/sessions/" + sessionId);
  await expect(page.getByRole("tab", { name: /web3/i })).toHaveCount(0);
  await page.getByLabel("Learning evidence").fill("Observed a local test transaction without writing proof on-chain.");
  await maybeScreenshot(page, testInfo.project.name, "desktop-1280", "09-web3-evidence-desktop.png");
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText(/waiting for Agent sync/i)).toBeVisible();
});
