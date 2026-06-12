import { test, expect } from "@playwright/test";

test.describe("AlphaGEmmaNOME Alignment & Feedback UI", () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to local dev server (default port 5173 or 5174)
    await page.goto("http://localhost:5173/");
  });

  test("should load the chat interface and sequence upload widget", async ({ page }) => {
    await expect(page.locator("header")).toContainText("AlphaGEmmaNOME");
    const input = page.locator("input[placeholder*='Ask about a gene']");
    await expect(input).toBeVisible();
    await expect(page.locator("text=Upload Fasta")).toBeVisible();
  });

  test("should render thumbs up/down and allow rating on assistant messages", async ({ page }) => {
    const input = page.locator("input[placeholder*='Ask about a gene']");
    await input.fill("Run accessibility optimization on H3K27ac track");
    await page.keyboard.press("Enter");

    // Wait for the busy indicator to finish streaming
    const sendBtn = page.locator("button:has-text('Send')");
    await expect(sendBtn).toBeVisible({ timeout: 15000 });

    // Verify rating buttons are visible for assistant messages
    const thumbsUp = page.locator("button[title='Thumbs up']");
    const thumbsDown = page.locator("button[title='Thumbs down']");
    await expect(thumbsUp).toBeVisible();
    await expect(thumbsDown).toBeVisible();

    // Click thumbs up and verify chosen preference is logged
    await thumbsUp.click();
    await expect(thumbsUp).toContainText("Liked");
  });

  test("should allow correcting reasoning steps inline on thought blocks", async ({ page }) => {
    const input = page.locator("input[placeholder*='Ask about a gene']");
    await input.fill("Silence locus X");
    await page.keyboard.press("Enter");

    // Wait for the thought block to render
    const thinkingHeader = page.locator("button:has-text('thinking')");
    await expect(thinkingHeader).toBeVisible({ timeout: 10000 });
    await thinkingHeader.click(); // expand thoughts

    // Click Correct Step to launch inline editor
    const correctBtn = page.locator("button:has-text('Correct Step')");
    await expect(correctBtn).toBeVisible();
    await correctBtn.click();

    // Fill new corrected text
    const textarea = page.locator("textarea");
    await expect(textarea).toBeVisible();
    await textarea.fill("This is a highly optimized, corrected expert trajectory for promoter silencing.");

    // Submit correction
    const saveBtn = page.locator("button:has-text('Save Expert Correction')");
    await saveBtn.click();

    // Check that success badge is shown
    await expect(page.locator("text=Correction Saved")).toBeVisible();
  });

  test("should toggle the Alignment Portal Dashboard and export datasets", async ({ page }) => {
    // 1. Toggle portal button in VizPane tab row
    const portalTab = page.locator("button:has-text('Alignment Portal')");
    await expect(portalTab).toBeVisible();
    await portalTab.click();

    // 2. Dashboard should display metric statistics cards
    await expect(page.locator("text=Agent Alignment Portal")).toBeVisible();
    await expect(page.locator("text=DPO Preference Pairs")).toBeVisible();
    await expect(page.locator("text=SFT Expert Corrections")).toBeVisible();

    // 3. Try clicking Export SFT Dataset
    const exportSftBtn = page.locator("button:has-text('Export SFT Dataset')");
    await expect(exportSftBtn).toBeVisible();

    // 4. Toggle back to tracks visualization
    const tracksTab = page.locator("button:has-text('Show Tracks')");
    await expect(tracksTab).toBeVisible();
    await tracksTab.click();
    await expect(page.locator("text=No visualizations yet.")).toBeVisible();
  });
});
