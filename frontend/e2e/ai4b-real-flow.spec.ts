import { expect, test } from "@playwright/test";

const evidenceText =
  "Append-only event replay rebuilds state by applying immutable events in sequence, preserving the history needed to reproduce the current view.";
const sourceUrl = "https://example.invalid/ai4b-local-fixture";
const sourceExplanation =
  "This local-only reference records an example event sequence and explains how replay reconstructs the same projection.";
const answerText =
  "Earlier events remain available, so replay can start from an empty state and deterministically apply the same ordered history again.";

test.setTimeout(60000);

test("completes and restores the real general flow through the Next BFF", async ({ page }) => {
  await page.goto("/");

  await page.getByLabel("Learning domain").selectOption("general");
  await page.getByLabel("Learning topic").fill("Deterministic event replay");
  await page
    .getByLabel("This session goal")
    .fill("Explain how an append-only event history makes deterministic replay possible.");
  await page.getByLabel("Expected output").fill("A concrete replay explanation");
  await page.getByRole("button", { name: /start session/i }).click();

  await expect(page).toHaveURL(/\/sessions\/[^/]+$/, { timeout: 15000 });
  await expect(page.getByRole("heading", { name: "Deterministic event replay" })).toBeVisible();
  await expect(page.getByText("Wallet metadata")).toHaveCount(0);
  await expect(page.getByText("Proof recording")).toHaveCount(0);

  await page.getByLabel("Learning notes, explanation, code, or error record").fill(evidenceText);
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText("Evidence submitted.")).toBeVisible();

  await page.getByRole("tab", { name: "URL" }).click();
  await page.getByLabel("Source URL").fill(sourceUrl);
  await page.getByLabel("Explanation of the linked content").fill(sourceExplanation);
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText("Evidence submitted.")).toBeVisible();

  await page.getByRole("button", { name: /end learning/i }).click();
  await expect(page.getByRole("status", { name: /review state/i })).toHaveText(
    /awaiting user/i
  );
  await expect(
    page.getByText("Explain why retaining earlier events makes replay reproducible.")
  ).toBeVisible();

  await page.getByLabel(/answer for /i).fill(answerText);
  await page.getByRole("button", { name: /submit answer/i }).click();
  await expect(page.getByText("Answer submitted.")).toBeVisible();
  await page.getByRole("button", { name: /request review again/i }).click();

  await expect(page.getByRole("status", { name: /review state/i })).toHaveText(/completed/i);
  await expect(page.getByText("LikelyLearning")).toBeVisible();
  await expect(
    page.getByText("The session shows credible learning evidence with explainable details.")
  ).toBeVisible();
  await expect(page.getByText("Proof recording disabled")).toBeVisible();

  const buildLog = page.getByRole("heading", { name: "Build Log" }).locator("..");
  await expect(buildLog.getByText("Session created")).toBeVisible();
  await expect(buildLog.getByText("Evidence submitted")).toHaveCount(2);
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

  await page.reload();

  await expect(page.getByRole("heading", { name: "Deterministic event replay" })).toBeVisible();
  await expect(page.getByText(evidenceText)).toBeVisible();
  await expect(page.getByText(sourceUrl)).toBeVisible();
  await expect(page.getByText(sourceExplanation)).toBeVisible();
  await expect(page.getByRole("status", { name: /review state/i })).toHaveText(/completed/i);
  await expect(page.getByText("LikelyLearning")).toBeVisible();
  await expect(buildLog.getByText("Review completed")).toBeVisible();
});
