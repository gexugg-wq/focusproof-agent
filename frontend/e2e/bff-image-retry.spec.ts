import { expect, test } from "@playwright/test";

const imageBytes = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64"
);

test("retries an unknown image result through the real BFF with the same key", async ({ page }, testInfo) => {
  const topic = `BFF image retry ${testInfo.project.name}`;
  await page.goto("/");
  await page.getByLabel("Learning topic").fill(topic);
  await page.getByLabel("This session goal").fill(
    "Prove that an unknown image upload result retries the identical operation safely."
  );
  await page.getByRole("button", { name: /start 25 minutes/i }).click();

  await expect(page).toHaveURL(/\/sessions\/[^/]+$/, { timeout: 15000 });
  await expect(page.getByRole("heading", { name: topic })).toBeVisible();
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.getByText(/Monad chain evidence/i)).toHaveCount(0);

  await page.getByLabel(/choose images/i).setInputFiles({
    name: "diagram.png",
    mimeType: "image/png",
    buffer: imageBytes
  });
  const composer = page.getByLabel("Learning evidence");
  await composer.fill("A bounded image used to prove retry identity.");
  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText(/Agent Runtime current unavailable/i)).toBeVisible();
  await expect(page.getByText("diagram.png")).toBeVisible();
  await expect(composer).toHaveValue("A bounded image used to prove retry identity.");

  await page.getByRole("button", { name: /submit evidence/i }).click();
  await expect(page.getByText(/image evidence uploaded/i)).toBeVisible();
  await expect(page.getByText("diagram.png")).toHaveCount(0);
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);
  await composer.fill("The same composer remains available after upload.");
  await expect(composer).toHaveValue("The same composer remains available after upload.");
});
