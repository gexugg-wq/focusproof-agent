import { expect, test } from "@playwright/test";

const evidenceText =
  "Append-only event replay rebuilds state by applying immutable events in sequence, preserving the ordered facts needed to reproduce the same result after restart.";
const answerText =
  "The same conversation keeps earlier ordered events available, so replay can deterministically rebuild the state from the durable history again.";

test("completes the official demo-deterministic two-review loop without runtime fallback", async ({ page }) => {
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

  await page.getByLabel("Learning evidence").fill(evidenceText);
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText("Evidence submitted.")).toBeVisible();

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
  await expect(page.getByText(evidenceText)).toBeVisible();

  const buildLog = page.getByRole("heading", { name: "Build Log" }).locator("..");
  await expect(buildLog.getByText("Session created")).toBeVisible();
  await expect(buildLog.getByText("Evidence submitted")).toHaveCount(1);
  await expect(buildLog.getByText("Question asked")).toBeVisible();
  await expect(buildLog.getByText("Answer submitted")).toBeVisible();
  await expect(buildLog.getByText("Review completed")).toBeVisible();
});
