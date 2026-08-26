import { expect, test } from "@playwright/test";

test("keeps the Monad panel hidden when the plugin capability is unavailable", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Learning topic").fill("General session");
  await page.getByLabel("This session goal").fill("Explain a general learning goal without Monad evidence.");
  await page.getByRole("button", { name: /start 25 minutes/i }).click();

  await expect(page).toHaveURL(/\/sessions\/sess_/, { timeout: 15000 });
  await expect(page.getByText(/Monad chain evidence/i)).toHaveCount(0);
});
