import { expect, test, type Page } from "@playwright/test";
const sessionId = "sess_image";
const capability = { capabilityId: "image_evidence", enabled: true, formats: ["image/png", "image/jpeg", "image/webp"], maxCount: 4, maxOriginalBytes: 10_485_760, maxNormalizedBytesPerSession: 20_971_520, explanationRequired: true };
const base = { sessionId, state: { sessionId, ownerUserId: "dev-anonymous-user", status: "running", goal: { domain: "general", title: "Diagram reasoning", goal: "Explain a causal diagram", expectedOutput: "explanation", plannedMinutes: 20 }, evidence: [], answers: {}, observations: [], previousActions: [], reviewResult: null, adapterMode: "openhands-local-real", conversationId: "conv_image", runtimeMode: "openhands-local-real" }, view: { productCapabilities: [capability] } };

async function mockSession(page: Page, enabled: boolean) {
  let attempts = 0;
  let uploaded = false;
  await page.route("**/api/focusproof/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/evidence/image")) {
      attempts += 1;
      if (attempts === 1) return route.fulfill({ status: 503, json: { code: "media_unavailable", retryable: true } });
      uploaded = true;
      return route.fulfill({ json: { evidenceId: "ev_image", mediaType: "image/png", normalizedBytes: 4, replayed: false } });
    }
    if (path.endsWith("/events")) return route.fulfill({ json: { events: [] } });
    if (path.endsWith("/reviews")) return route.fulfill({ json: { reviews: [] } });
    if (path.includes("/sessions/")) return route.fulfill({ json: { ...base, state: { ...base.state, evidence: uploaded ? [{ evidenceId: "ev_image", evidenceType: "image", contentHash: "sha256:safe", textContent: "The diagram connects causes to outcomes.", sourceUrl: null, metadata: { mediaType: "image/png", normalizedBytes: 4 } }] : [] }, view: { productCapabilities: enabled ? [capability] : [] } } });
    return route.fulfill({ json: {} });
  });
}
async function shot(page: Page, project: string, name: string) {
  if (project === "chromium" || project === "mobile") await page.screenshot({ path: `test-results/acceptance/ai5/${project}-${name}.png`, fullPage: true });
}

test("image evidence is hidden when the backend capability is off", async ({ page }, info) => {
  await mockSession(page, false);
  await page.goto("/sessions/sess_image");
  await expect(page.getByRole("heading", { name: /image evidence/i })).toHaveCount(0);
  await shot(page, info.project.name, "capability-off");
});

test("image evidence follows capability limits and supports upload recovery", async ({ page }, info) => {
  await mockSession(page, true);
  await page.goto("/sessions/sess_image");
  await page.getByLabel(/choose images/i).setInputFiles({ name: "diagram.png", mimeType: "image/png", buffer: Buffer.from([137, 80, 78, 71]) });
  await page.getByLabel(/learning evidence/i).fill("The diagram connects causes to outcomes.");
  await shot(page, info.project.name, "selected");
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText(/Agent Runtime current unavailable/i)).toBeVisible();
  await expect(page.getByText("diagram.png")).toBeVisible();
  await shot(page, info.project.name, "retryable-error");
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText(/image evidence uploaded/i)).toBeVisible();
  await shot(page, info.project.name, "success");
  await page.reload();
  await expect(page.getByLabel("Submitted evidence").getByText("The diagram connects causes to outcomes.")).toBeVisible();
  await expect(page.locator("img")).toHaveCount(0);
});
