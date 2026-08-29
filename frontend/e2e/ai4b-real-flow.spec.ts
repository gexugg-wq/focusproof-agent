import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import path from "node:path";

const assetsDir = path.resolve(__dirname, "../test-results/acceptance/ai4b");
const longGoal =
  "Explain how an append-only event history makes deterministic replay possible, including why immutable ordering preserves earlier facts, how a projection can be rebuilt from an empty state after a clean process restart, and which verification boundary prevents a mutable view from silently replacing the durable learning record.";
const evidenceText =
  "Append-only event replay rebuilds state by applying immutable events in sequence. Earlier facts remain available, each projection step is deterministic, and a disposable view can be regenerated without rewriting the durable history that explains how the current result was produced.";
const failedEvidenceText =
  "Keep this replay explanation after failure; retry must not erase learner input.";

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
  if (testInfo.project.metadata.visualCapture !== true) return;
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

  const topic = `Deterministic event replay ${testInfo.project.name}`;
  await page.getByLabel("Learning topic").fill(topic);
  await page.getByLabel("This session goal").fill(longGoal);
  await page.getByRole("button", { name: /start 25 minutes/i }).click();

  await expect(page).toHaveURL(/\/sessions\/[^/]+$/, { timeout: 15000 });
  await expect(page.getByRole("heading", { name: topic })).toBeVisible();
  await expect(page.getByText("Wallet metadata")).toHaveCount(0);
  await expect(page.getByText("Proof recording")).toHaveCount(0);
  await expectWrapped(page.getByText(longGoal, { exact: true }));
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);
  const textInput = page.getByLabel("Learning evidence");
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
      page.getByRole("heading", { name: "Submit evidence", exact: true }),
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
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expectGeometry(page, [
    page.getByRole("heading", { name: "Submit evidence", exact: true }),
    page.getByText(evidenceText, { exact: true }),
    page.getByRole("heading", { name: "Build Log" })
  ]);
  await expectWrapped(page.getByText(longGoal, { exact: true }));
});
