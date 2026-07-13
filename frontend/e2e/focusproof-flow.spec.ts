import { expect, test, type Page, type Route } from "@playwright/test";

const sessionId = "sess_mock_ai3";

const sessionPayload = {
  sessionId,
  state: {
    sessionId,
    ownerUserId: "dev-anonymous-user",
    status: "running",
    goal: { domain: "web3", title: "Understand transaction receipts", goal: "Explain a transaction receipt and emitted event.", expectedOutput: "review note", plannedMinutes: 25 },
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
    dimensions: { evidence: 85, explanation: 82 },
    findings: [{ severity: "info", message: "Specific transaction evidence was explained.", evidenceIds: [], observationRefs: [] }],
    summary: "The evidence supports the session goal.",
    nextStep: "Compare this receipt with a failed transaction."
  }
};

async function mockApi(page: Page) {
  let reviewCount = 0;
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
        return route.fulfill({ json: { sessionId, conversationMode: "openhands-local-real", usedOpenHandsConversation: true, reviewStatus: "awaiting_user", agentQuestions: [{ questionId: "q1", question: "What did the event prove?" }] } });
      }
      return route.fulfill({ json: completedReview });
    }
    if (path.endsWith("/events")) {
      return route.fulfill({ json: { events: [
        { id: "evt_3", sessionId, type: "review.completed", sequence: 3, createdAt: "now", actor: "agent", payload: {} },
        { id: "evt_1", sessionId, type: "session.created", sequence: 1, createdAt: "now", actor: "system", payload: {} },
        { id: "evt_2", sessionId, type: "evidence.submitted", sequence: 2, createdAt: "now", actor: "user", payload: {} }
      ] } });
    }
    if (path.includes("/sessions/")) return route.fulfill({ json: sessionPayload });
    return route.fulfill({ status: 404, json: { code: "not_found" } });
  });
}

test("completes the mocked FocusProof review loop and refresh recovery", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByLabel("Learning domain").selectOption("web3");
  await page.getByLabel("Learning topic").fill("Understand transaction receipts");
  await page.getByLabel("This session goal").fill("Explain a transaction receipt and emitted event.");
  await page.getByLabel("Expected output").fill("review note");
  await page.getByRole("button", { name: /start session/i }).click();
  await expect(page).toHaveURL(/sessions\/sess_mock_ai3/);
  await expect(page.getByText("Wallet metadata")).toBeVisible();
  await page.getByRole("tab", { name: /web3/i }).click();
  await page.getByLabel("Transaction hash").fill("0xabc");
  await page.getByLabel("Chain ID or network name").fill("10143");
  await page.getByLabel("What did this operation complete?").fill("Emitted a Transfer event.");
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText(/waiting for Agent sync/i)).toBeVisible();
  await page.getByRole("button", { name: /end learning/i }).click();
  await expect(page.getByText(/what did the event prove/i)).toBeVisible();
  await page.getByLabel(/answer for q1/i).fill("The event proves the transfer was emitted by the contract.");
  await page.getByRole("button", { name: /submit answer/i }).click();
  await page.getByRole("button", { name: /request review again/i }).click();
  await expect(page.getByText("84")).toBeVisible();
  await expect(page.getByText(/not a judgment of the learner/i)).toBeVisible();
  await expect(page.getByText(/Session created/i)).toBeVisible();
  await page.reload();
  await expect(page.getByText("Understand transaction receipts")).toBeVisible();
  await page.screenshot({ path: "../docs/research/assets/ai3/" + testInfo.project.name + "-session.png", fullPage: true });
});

test("shows retryable 409 and 503 errors without success copy", async ({ page }) => {
  let status = 409;
  await page.route("**/api/focusproof/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.includes("/sessions/") && !path.endsWith("/review") && !path.endsWith("/events")) return route.fulfill({ json: sessionPayload });
    if (path.endsWith("/events")) return route.fulfill({ json: { events: [] } });
    if (path.endsWith("/review")) {
      const current = status;
      status = 503;
      return route.fulfill({ status: current, json: { code: current === 409 ? "session_busy" : "runtime_unavailable", retryable: true } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("/sessions/" + sessionId);
  await page.getByRole("button", { name: /end learning/i }).click();
  await expect(page.getByText(/processing|runtime|failed/i)).toBeVisible();
});
