import { expect, test } from "@playwright/test";

const wallet = "0xde709f2102306220921060314715629080e2fb77";
const txHash = "0x" + "ab".repeat(32);
const contract = "0x52908400098527886E0F7030069857D2E4169EE7";
const explanation = "I used the deterministic demo transaction to call increment().";

test("completes the Monad demo flow and shows verification in the Build Log", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Learning topic").fill("Counter state transitions");
  await page.getByLabel("This session goal").fill("Explain why increment() changes only the caller state.");
  await page.getByRole("button", { name: /start 25 minutes/i }).click();

  await expect(page).toHaveURL(/\/sessions\/sess_/, { timeout: 15000 });
  await expect(page.getByRole("heading", { name: /Monad chain evidence/i })).toBeVisible();
  await expect(page.getByText(/Submit a wallet transaction that calls increment\(\) on the configured teaching contract/i)).toBeVisible();

  await page.getByLabel(/wallet address/i).fill(wallet);
  await page.getByLabel(/transaction hash/i).fill(txHash);
  await page.getByLabel(/contract address/i).fill(contract);
  await page.getByLabel(/operation explanation/i).fill(explanation);
  await page.getByRole("button", { name: /submit monad evidence/i }).click();
  await expect(page.getByText(/Monad evidence saved, waiting for Agent sync|Monad evidence submitted/i)).toBeVisible();

  await page.getByRole("button", { name: /end learning/i }).click();

  await expect(page.getByText(/Verification completed/i)).toBeVisible();
  await expect(page.getByText(/Review completed/i)).toBeVisible();
  await expect(page.getByText(/Counter state transitions/i)).toBeVisible();
});
