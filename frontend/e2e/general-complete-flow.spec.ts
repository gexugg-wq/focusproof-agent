import { expect, test } from "@playwright/test";

const evidenceText =
  "Append-only event replay rebuilds state by applying immutable events in sequence, preserving the history needed to reproduce the current view.";
const answerText =
  "Earlier events remain available, so replay can start from an empty state and deterministically apply the same ordered history again.";
const findingText = "The session shows credible learning evidence with explainable details.";

test("completes one Chromium general review in the same conversation across reload", async ({ page }) => {
  test.setTimeout(180000);
  const topic = "Deterministic replay complete flow";
  await page.goto("/");
  await page.getByLabel("Learning topic").fill(topic);
  await page.getByLabel("This session goal").fill(
    "Explain how stable event identity supports deterministic replay."
  );
  await page.getByRole("button", { name: /start 25 minutes/i }).click();

  await expect(page).toHaveURL(/\/sessions\/[^/]+$/, { timeout: 15000 });
  const sessionUrl = page.url();
  await expect(page.getByRole("heading", { name: topic })).toBeVisible();
  await expect(page.getByText("general", { exact: true })).toBeVisible();
  await expect(page.getByText("Not specified", { exact: true })).toBeVisible();
  await expect(page.getByText("25", { exact: true })).toBeVisible();
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.getByText(/Monad chain evidence/i)).toHaveCount(0);

  const composer = page.getByLabel("Learning evidence");
  await composer.fill(evidenceText);
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText("Evidence submitted.")).toBeVisible();
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);

  await page.getByRole("button", { name: /end learning/i }).click();
  const reviewState = page.getByRole("status", { name: /review state/i });
  await expect(reviewState).toHaveText(/awaiting user/i, { timeout: 75000 });
  const question = page.getByText(
    "Explain why retaining earlier events makes replay reproducible."
  );
  await expect(question).toBeVisible();
  await page.getByLabel(/answer for /i).fill(answerText);
  await page.getByRole("button", { name: /submit answer/i }).click();
  await expect(page.getByText("Answer submitted.")).toBeVisible();
  await page.getByRole("button", { name: /request review again/i }).click();

  await expect(reviewState).toHaveText(/completed/i, { timeout: 30000 });
  await expect(page.getByText("LikelyLearning")).toBeVisible();
  await expect(page.getByText(findingText)).toBeVisible();
  const buildLog = page.getByRole("heading", { name: "Build Log" }).locator("..");
  await expect(buildLog.getByText("Session created")).toBeVisible();
  await expect(buildLog.getByText("Evidence submitted")).toHaveCount(1);
  await expect(buildLog.getByText("Verification requested")).toBeVisible();
  await expect(buildLog.getByText("Verification completed")).toBeVisible();
  await expect(buildLog.getByText("Question asked")).toBeVisible();
  await expect(buildLog.getByText("Answer submitted")).toBeVisible();
  await expect(buildLog.getByText("Review completed")).toBeVisible();
  const sequences = await buildLog.locator("ol > li").evaluateAll((items) =>
    items.map((item) => Number(item.textContent?.match(/#(\d+)/)?.[1]))
  );
  expect(sequences).toEqual([...sequences].sort((left, right) => left - right));
  expect(new Set(sequences).size).toBe(sequences.length);

  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);
  await page.reload();
  await expect(page).toHaveURL(sessionUrl);
  await expect(page.getByRole("heading", { name: topic })).toBeVisible();
  await expect(page.getByText(evidenceText)).toBeVisible();
  await expect(reviewState).toHaveText(/completed/i);
  await expect(page.getByText("LikelyLearning")).toBeVisible();
  await expect(buildLog.getByText("Review completed")).toBeVisible();
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.getByText(/Monad chain evidence/i)).toHaveCount(0);
});
