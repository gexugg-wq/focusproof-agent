import { expect, test } from "@playwright/test";
import path from "node:path";

const imageFixturePath = path.resolve(__dirname, "../../agent-server/tests/fixtures/real-vision/focusproof-general-session.png");
const explanationText =
  "The uploaded PNG preserves the session capture, and the explanation ties it back to deterministic replay evidence.";
const answerText =
  "The same conversation keeps the uploaded image evidence and my explanation attached to the durable history before the final review completes.";
const textOnlyEvidence =
  "Deterministic replay keeps the same ordered native events available after restart.";
const textOnlyAnswer =
  "Because the conversation reuses the same durable event history, replay reconstructs the same state instead of inventing a new thread.";

test("completes the official demo-deterministic two-review loop for text-only evidence", async ({ page }) => {
  test.setTimeout(180000);
  const topic = "Demo deterministic text-only";
  await page.goto("/");
  await page.getByLabel("Learning topic").fill(topic);
  await page.getByLabel("This session goal").fill(
    "Explain how durable event identity keeps deterministic replay stable across repeated reviews."
  );
  await page.getByRole("button", { name: /start 25 minutes/i }).click();

  await expect(page).toHaveURL(/\/sessions\/[^/]+$/, { timeout: 15000 });
  await expect(page.getByRole("heading", { name: topic })).toBeVisible();
  await expect(page.getByText(/runtime unavailable/i)).toHaveCount(0);

  await page.getByLabel("Learning evidence").fill(textOnlyEvidence);
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText("Evidence submitted.")).toBeVisible();
  await expect(page.getByText(/runtime unavailable/i)).toHaveCount(0);

  await page.getByRole("button", { name: /end learning/i }).click();
  const reviewState = page.getByRole("status", { name: /review state/i });
  await expect(reviewState).toHaveText(/awaiting user/i, { timeout: 75000 });
  await expect(page.getByText("Explain why native event continuity matters after restart.")).toBeVisible();

  await page.getByLabel(/answer for /i).fill(textOnlyAnswer);
  await page.getByRole("button", { name: /submit answer/i }).click();
  await expect(page.getByText("Answer submitted.")).toBeVisible();
  await page.getByRole("button", { name: /request review again/i }).click();

  await expect(reviewState).toHaveText(/completed/i, { timeout: 30000 });
  await expect(page.getByText(/runtime unavailable/i)).toHaveCount(0);
  await expect(page.getByText(textOnlyEvidence)).toBeVisible();
  await expect(page.getByText("image/png")).toHaveCount(0);
});

test("completes the official demo-deterministic two-review loop with one text plus PNG evidence submission", async ({ page }) => {
  test.setTimeout(180000);
  const topic = "Demo deterministic review loop";
  await page.goto("/");
  await page.getByLabel("Learning topic").fill(topic);
  await page.getByLabel("This session goal").fill(
    "Explain how durable event identity keeps deterministic replay stable across repeated reviews."
  );
  await page.getByRole("button", { name: /start 25 minutes/i }).click();

  await expect(page).toHaveURL(/\/sessions\/[^/]+$/, { timeout: 15000 });
  await expect(page.getByRole("heading", { name: topic })).toBeVisible();
  await expect(page.getByText(/runtime unavailable/i)).toHaveCount(0);

  await page.getByLabel(/choose images/i).setInputFiles(imageFixturePath);
  await expect(page.getByText("focusproof-general-session.png")).toBeVisible();
  await page.getByLabel("Learning evidence").fill(explanationText);
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText("Image evidence uploaded.")).toBeVisible();
  await expect(page.getByText(/runtime unavailable/i)).toHaveCount(0);

  await page.getByRole("button", { name: /end learning/i }).click();
  const reviewState = page.getByRole("status", { name: /review state/i });
  await expect(reviewState).toHaveText(/awaiting user/i, { timeout: 75000 });
  await expect(page.getByText("Explain why native event continuity matters after restart.")).toBeVisible();

  await page.getByLabel(/answer for /i).fill(answerText);
  await page.getByRole("button", { name: /submit answer/i }).click();
  await expect(page.getByText("Answer submitted.")).toBeVisible();
  await page.getByRole("button", { name: /request review again/i }).click();

  await expect(reviewState).toHaveText(/completed/i, { timeout: 30000 });
  await expect(page.getByText(/runtime unavailable/i)).toHaveCount(0);
  await expect(page.getByText(explanationText)).toBeVisible();
  await expect(page.getByText("image/png")).toHaveCount(1);

  const sessionId = /\/sessions\/([^/]+)$/.exec(page.url())?.[1];
  expect(sessionId).toBeTruthy();

  const sessionState = await page.evaluate(async (id) => {
    const response = await fetch(`/api/focusproof/sessions/${id}`);
    return {
      ok: response.ok,
      body: await response.json()
    };
  }, sessionId);
  expect(sessionState.ok).toBeTruthy();
  const imageEvidence = sessionState.body.state.evidence.filter(
    (item: { evidenceType: string; metadata: { mediaType?: string } }) =>
      item.evidenceType === "image/png" || item.metadata?.mediaType === "image/png"
  );
  expect(imageEvidence).toHaveLength(1);

  const buildLog = page.getByRole("heading", { name: "Build Log" }).locator("..");
  await expect(buildLog.getByText("Session created")).toBeVisible();
  await expect(buildLog.getByText("Evidence submitted")).toHaveCount(1);
  await expect(buildLog.getByText("Question asked")).toBeVisible();
  await expect(buildLog.getByText("Answer submitted")).toBeVisible();
  await expect(buildLog.getByText("Review completed")).toBeVisible();
});
