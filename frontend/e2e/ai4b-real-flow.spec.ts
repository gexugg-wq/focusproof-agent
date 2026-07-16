import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import path from "node:path";

const assetsDir = path.resolve(__dirname, "../../docs/research/assets/ai4b");
const longGoal =
  "Explain how an append-only event history makes deterministic replay possible, including why immutable ordering preserves earlier facts, how a projection can be rebuilt from an empty state, and which verification boundary prevents a mutable view from silently replacing the durable learning record.";
const evidenceText =
  "Append-only event replay rebuilds state by applying immutable events in sequence. Earlier facts remain available, each projection step is deterministic, and a disposable view can be regenerated without rewriting the durable history that explains how the current result was produced.";
const sourceUrl =
  "https://example.invalid/ai4b-local-fixture/deterministic-replay/verification-boundary/" +
  "immutable-event-history-projection-reconstruction-without-hidden-mutable-state";
const sourceExplanation =
  "This local-only reference records a concrete event sequence, explains why each immutable fact remains independently inspectable, and shows how replay reconstructs the same projection after the derived view is discarded.";
const answerText =
  "Earlier events remain available, so replay can start from an empty state and deterministically apply the same ordered history again.";
const failedEvidenceText =
  "Keep this replay explanation after failure; retry must not erase learner input.";
const findingText = "The session shows credible learning evidence with explainable details.";

type DocumentRect = {
  bottom: number;
  height: number;
  left: number;
  right: number;
  top: number;
  width: number;
};

test.setTimeout(60000);

function panels(page: Page): Locator[] {
  return [
    page.locator("main > aside"),
    page.locator("section[aria-labelledby='evidence-heading']"),
    page.locator("section[aria-labelledby='review-heading']"),
    page.locator("section[aria-labelledby='build-log-heading']")
  ];
}

async function documentRect(locator: Locator): Promise<DocumentRect> {
  return locator.evaluate((node) => {
    const rect = node.getBoundingClientRect();
    return {
      bottom: rect.bottom + window.scrollY,
      height: rect.height,
      left: rect.left + window.scrollX,
      right: rect.right + window.scrollX,
      top: rect.top + window.scrollY,
      width: rect.width
    };
  });
}

function intersects(left: DocumentRect, right: DocumentRect): boolean {
  return (
    left.left < right.right &&
    left.right > right.left &&
    left.top < right.bottom &&
    left.bottom > right.top
  );
}

async function expectWrapped(locator: Locator): Promise<void> {
  const measurements = await locator.evaluateAll((nodes) =>
    nodes.map((node) => {
      const element = node as HTMLElement;
      return {
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        whiteSpace: getComputedStyle(element).whiteSpace
      };
    })
  );
  expect(measurements.length).toBeGreaterThan(0);
  for (const measurement of measurements) {
    expect(measurement.clientWidth).toBeGreaterThan(0);
    expect(measurement.scrollWidth).toBeLessThanOrEqual(measurement.clientWidth + 1);
    expect(measurement.whiteSpace).not.toBe("nowrap");
  }
}

async function settleLayout(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    });
  });
}

async function expectGeometry(page: Page, focalElements: Locator[]): Promise<void> {
  await settleLayout(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  const bodyWidth = await page.locator("body").evaluate((node) => ({
    clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth
  }));
  expect(bodyWidth.scrollWidth).toBeLessThanOrEqual(bodyWidth.clientWidth + 1);

  const documentHeight = await page.locator("html").evaluate((node) => node.scrollHeight);
  const panelRects = await Promise.all(panels(page).map((panel) => documentRect(panel)));
  for (const rect of panelRects) {
    expect(rect.width).toBeGreaterThan(0);
    expect(rect.height).toBeGreaterThan(0);
    expect(rect.left).toBeGreaterThanOrEqual(-1);
    expect(rect.right).toBeLessThanOrEqual(viewport!.width + 1);
    expect(rect.top).toBeGreaterThanOrEqual(0);
    expect(rect.bottom).toBeLessThanOrEqual(documentHeight + 1);
  }
  for (let left = 0; left < panelRects.length; left += 1) {
    for (let right = left + 1; right < panelRects.length; right += 1) {
      expect(intersects(panelRects[left], panelRects[right])).toBe(false);
    }
  }

  for (const focalElement of focalElements) {
    await focalElement.scrollIntoViewIfNeeded();
    const box = await focalElement.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThan(0);
    expect(box!.height).toBeGreaterThan(0);
    expect(box!.x).toBeGreaterThanOrEqual(-1);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport!.width + 1);
    expect(box!.y).toBeGreaterThanOrEqual(-1);
    expect(box!.y + box!.height).toBeLessThanOrEqual(viewport!.height + 1);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

async function capture(page: Page, testInfo: TestInfo, project: string, fileName: string) {
  if (testInfo.project.name !== project) return;
  await settleLayout(page);
  await expect(page.locator("nextjs-portal")).toHaveCount(0);
  await page.screenshot({
    path: path.join(assetsDir, fileName),
    fullPage: true,
    scale: "css"
  });
}

test("captures four-viewport geometry through the real Next BFF flow", async ({ page }, testInfo) => {
  await page.goto("/");

  await page.getByLabel("Learning domain").selectOption("general");
  await page.getByLabel("Learning topic").fill("Deterministic event replay");
  await page.getByLabel("This session goal").fill(longGoal);
  await page
    .getByLabel("Expected output")
    .fill("A concrete replay explanation with an independently checkable reconstruction boundary");
  await page.getByRole("button", { name: /start session/i }).click();

  await expect(page).toHaveURL(/\/sessions\/[^/]+$/, { timeout: 15000 });
  await expect(page.getByRole("heading", { name: "Deterministic event replay" })).toBeVisible();
  await expect(page.getByText("Wallet metadata")).toHaveCount(0);
  await expect(page.getByText("Proof recording")).toHaveCount(0);
  await expectWrapped(page.getByText(longGoal, { exact: true }));

  const textInput = page.getByLabel("Learning notes, explanation, code, or error record");
  if (testInfo.project.name === "mobile-360") {
    await page.route("**/api/focusproof/sessions/*/evidence", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ code: "backend_unavailable", retryable: true })
        });
        return;
      }
      await route.continue();
    });
    await textInput.fill(failedEvidenceText);
    await page.getByRole("button", { name: /submit evidence/i }).click();
    await expect(
      page.getByText("Agent Runtime current unavailable. Page data has been preserved.")
    ).toBeVisible();
    await expect(textInput).toHaveValue(failedEvidenceText);
    await textInput.evaluate((node) => {
      node.scrollTop = 0;
      node.scrollLeft = 0;
    });
    await expectGeometry(page, [
      page.getByRole("heading", { name: "Evidence", exact: true }),
      page.getByText("Agent Runtime current unavailable. Page data has been preserved."),
      page.getByRole("button", { name: /submit evidence/i })
    ]);
    await expectWrapped(page.getByText(longGoal, { exact: true }));
    await expectWrapped(page.getByText("Agent Runtime current unavailable. Page data has been preserved."));
    await capture(page, testInfo, "mobile-360", "360x800-failed-input-preserved.png");
    return;
  }

  await textInput.fill(evidenceText);
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText("Evidence submitted.")).toBeVisible();

  await page.getByRole("tab", { name: "URL" }).click();
  await page.getByLabel("Source URL").fill(sourceUrl);
  await page.getByLabel("Explanation of the linked content").fill(sourceExplanation);
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText("Evidence submitted.")).toBeVisible();
  await page.getByRole("tab", { name: "Text" }).click();

  await page.getByRole("button", { name: /end learning/i }).click();
  const reviewState = page.getByRole("status", { name: /review state/i });
  await expect(reviewState).toHaveText(/awaiting user/i);
  const question = page.getByText(
    "Explain why retaining earlier events makes replay reproducible."
  );
  await expect(question).toBeVisible();

  if (testInfo.project.name === "mobile") {
    await expectGeometry(page, [
      page.getByRole("heading", { name: "Agent review" }),
      reviewState,
      question,
      page.getByRole("button", { name: /submit answer/i })
    ]);
    await expectWrapped(page.getByText(longGoal, { exact: true }));
    await expectWrapped(question);
    await expectWrapped(page.locator("section[aria-labelledby='build-log-heading'] li"));
    await capture(page, testInfo, "mobile", "390x844-awaiting-user.png");
    return;
  }

  await page.getByLabel(/answer for /i).fill(answerText);
  await page.getByRole("button", { name: /submit answer/i }).click();
  await expect(page.getByText("Answer submitted.")).toBeVisible();
  await page.getByRole("button", { name: /request review again/i }).click();

  await expect(reviewState).toHaveText(/completed/i);
  await expect(page.getByText("LikelyLearning")).toBeVisible();
  await expect(page.getByText(findingText)).toBeVisible();
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
  await expect(reviewState).toHaveText(/completed/i);
  await expect(page.getByText("LikelyLearning")).toBeVisible();
  await expect(buildLog.getByText("Review completed")).toBeVisible();

  await expectGeometry(page, [
    page.getByRole("heading", { name: "Evidence", exact: true }),
    page.getByRole("heading", { name: "Agent review" }),
    reviewState,
    page.getByText(findingText),
    page.getByRole("heading", { name: "Build Log" })
  ]);
  await expectWrapped(page.getByText(longGoal, { exact: true }));
  await expectWrapped(page.getByText(evidenceText, { exact: true }));
  await expectWrapped(page.getByText(sourceUrl, { exact: true }));
  await expectWrapped(page.getByText(sourceExplanation, { exact: true }));
  await expectWrapped(page.getByText(findingText));
  await expectWrapped(buildLog.locator("li"));

  await capture(page, testInfo, "chromium", "1440x900-completed.png");
  await capture(page, testInfo, "desktop-1280", "1280x720-completed.png");
});
