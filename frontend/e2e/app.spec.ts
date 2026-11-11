import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const MEDIA = path.resolve(__dirname, "../../backend/tests/media");
const real = process.env.E2E_REAL === "1";

async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

async function uploadAndWait(page: Page, file: string) {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles(path.join(MEDIA, file));
  await page.getByRole("button", { name: "Generate transcript" }).click();
  await expect(page.getByRole("list", { name: "Processing steps" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible({ timeout: real ? 280_000 : 45_000 });
}

test("home page renders and has no horizontal overflow", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Turn videos into study-ready notes." })).toBeVisible();
  await expect(page.getByLabel("Video link")).toBeVisible();
  await expect(page.getByText(/Drop an audio or video file here/)).toBeVisible();
  await noHorizontalOverflow(page);
});

test("theme toggle persists across reloads", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  // applied by the inline head script before React loads, so there is no light flash
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(bg).toBe("rgb(17, 18, 20)");
});

test("system dark theme is respected on first visit", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("source indicator flags unsupported links", async ({ page }) => {
  await page.goto("/");
  const input = page.getByLabel("Video link");
  await input.fill("https://example.com/some-article");
  await expect(page.getByText("Not supported", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Generate transcript" })).toBeDisabled();
  await input.fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  await expect(page.getByText("YouTube", { exact: true })).toBeVisible();
});

test("corrupt upload shows a friendly error", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles(path.join(MEDIA, "corrupt.mp4"));
  await page.getByRole("button", { name: "Generate transcript" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "audio or video" })).toContainText("be read as audio or video");
});

test("full upload workflow", async ({ page, context }, info) => {
  const desktop = info.project.name === "desktop";
  if (desktop) await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await uploadAndWait(page, "lecture.m4a");
  await expect(page.getByRole("heading", { level: 1, name: "lecture" })).toBeVisible();
  await expect(page.getByText("Executive summary")).toBeVisible();
  await noHorizontalOverflow(page);

  // key point -> highlighted transcript passage
  if (desktop) {
    const sidebar = page.getByRole("complementary", { name: "Study notes" });
    await sidebar.getByRole("button").filter({ hasText: /\d\d:\d\d:\d\d/ }).first().click();
  } else {
    await page.getByText("Study notes: overview, key points, topics").click();
    await page.locator("details").getByRole("button").filter({ hasText: /\d\d:\d\d:\d\d/ }).first().click();
  }
  await expect(page.getByRole("tab", { name: "Full transcript" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".seg-hl").first()).toBeVisible();

  // search
  await page.getByLabel("Search transcript").fill(real ? "water" : "photosynthesis");
  await expect(page.getByText(/^1 of \d+$/)).toBeVisible();
  await noHorizontalOverflow(page);

  // copy full transcript
  await page.getByRole("button", { name: "Copy with timestamps" }).click();
  await expect(page.getByText("Transcript with timestamps copied")).toBeVisible();
  if (desktop) {
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip.startsWith("[00:00:00]")).toBe(true);
    const lastSeg = await page.locator('[id^="seg-"]').last().innerText();
    expect(clip).toContain(lastSeg.trim());
  }

  await page.getByRole("tab", { name: "Topics" }).click();
  await expect(page.getByRole("heading", { name: "Topic outline" })).toBeVisible();

  // citation editing updates the reference
  await page.getByRole("tab", { name: "Citation" }).click();
  await page.getByLabel("Creator (person or organization)").fill("Dana Whitfield");
  await page.getByLabel("The creator is a person").check();
  await page.getByLabel("Published").fill("2024-03-15");
  await expect(page.getByText("Whitfield, D. (2024, March 15).").first()).toBeVisible({ timeout: 10_000 });
  await page.getByRole("radio", { name: "MLA 9" }).click();
  await expect(page.getByText("Whitfield, Dana.").first()).toBeVisible();

  // all four exports download
  await page.getByRole("tab", { name: "Export" }).click();
  const formats: [RegExp, string][] = [[/^PDF/, "pdf"], [/^Word/, "docx"], [/^Markdown/, "md"], [/^Plain text/, "txt"]];
  for (const [label, ext] of formats) {
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: label }).click()]);
    expect(download.suggestedFilename()).toMatch(new RegExp(`\\.${ext}$`));
    expect(await download.path()).toBeTruthy();
  }

  // the job URL restores results in the same browser
  const url = page.url();
  await page.goto("/");
  await expect(page.getByText("Recent in this browser")).toBeVisible();
  await page.goto(url);
  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible();

  // delete
  await page.getByRole("tab", { name: "Export" }).click();
  await page.getByRole("button", { name: "Delete result" }).click();
  await page.getByRole("button", { name: "Yes, delete permanently" }).click();
  await expect(page.getByRole("heading", { name: "Turn videos into study-ready notes." })).toBeVisible();
  await page.goto(url);
  await expect(page.getByText("Result not available")).toBeVisible();
});
