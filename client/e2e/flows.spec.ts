import { expect, test } from "@playwright/test";
import path from "node:path";

const SAMPLE = path.resolve(new URL("../../samples/synthetic/synthetic_bench_pickup.mp4", import.meta.url).pathname);

test.beforeEach(async ({ page }) => {
  // The browser must never talk to a model provider directly.
  await page.route(/api\.meta\.ai/, (route) => route.abort());
});

test("upload, select by drawing a box, analyse in fixture mode, ask, cite and verify", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "the full flow runs on desktop; the phone project checks layout");
  await page.goto("/");
  await page.getByLabel("Video file").setInputFiles(SAMPLE);
  await page.getByLabel(/I own this footage/).check();
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("heading", { name: "Select the object to follow" })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".banner-synthetic")).toBeVisible();
  await page.getByLabel("Draw a box on this frame").check();
  const frame = page.locator(".selector-frame");
  const box = (await frame.boundingBox())!;
  // The bottle sits near x 511-529, y 361-405 of 960x540; the frame image fills the 16:9 holder.
  const sx = box.width / 960;
  await page.mouse.move(box.x + 509 * sx, box.y + 359 * sx);
  await page.mouse.down();
  await page.mouse.move(box.x + 531 * sx, box.y + 407 * sx, { steps: 4 });
  await page.mouse.up();
  await page.getByLabel(/Development fixture/).check();
  await page.getByRole("button", { name: "Analyse this object" }).click();
  await expect(page.locator(".banner-fixture")).toContainText("Development fixture. Not a real video analysis.");
  await expect(page.locator(".event-list li").first()).toBeVisible({ timeout: 90_000 });
  await expect(page.locator(".event.status-rejected")).toContainText("Pick-up candidate");
  await page.locator(".event.status-rejected summary").click();
  await expect(page.locator(".event.status-rejected")).toContainText("closeness is not evidence of contact");
  await page.getByRole("button", { name: "Who picked it up?" }).click();
  const citation = page.locator(".citation").first();
  await expect(citation).toBeVisible({ timeout: 60_000 });
  await citation.click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer.locator("figure img").first()).toBeVisible();
  await drawer.getByRole("tab", { name: "Annotated copies" }).click();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(citation).toBeFocused();
  await page.getByRole("button", { name: "Create sealed export" }).click();
  await page.getByRole("button", { name: "Verify against the server registry" }).click({ timeout: 60_000 });
  await expect(page.locator(".verify-result")).toContainText("Integrity valid");
  await expect(page.locator(".verify-result")).toContainText("anchored to the server registry");
});

test("recorded demonstration replays with its labels and original cost", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open recorded run" }).first().click();
  await expect(page.locator(".banner-recorded")).toContainText("Replayed without new model calls");
  await expect(page.locator(".banner-synthetic")).toBeVisible();
  const tabs = page.locator(".tabs.mobile-only");
  if (await tabs.isVisible()) await tabs.getByRole("tab", { name: "History" }).click();
  await expect(page.locator(".event-list li").first()).toBeVisible();
  if (await tabs.isVisible()) await tabs.getByRole("tab", { name: "Answer" }).click();
  await expect(page.locator(".answer-card").first()).toBeVisible();
  await expect(page.getByText(/recorded demonstration replays its recorded questions/)).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("overlay toggle and seeking across the occlusion", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open recorded run" }).last().click();
  await expect(page.locator("video")).toBeVisible();
  await page.locator("video").evaluate((v: HTMLVideoElement) => { v.currentTime = 9.0; });
  await expect(page.locator(".video-status")).toContainText(/not detected|unknown/, { timeout: 15_000 });
  await page.getByLabel("Show overlays").uncheck();
  await expect(page.locator("svg.overlay")).toHaveCount(0);
});

test("switching between analyses never shows another clip's tracks", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open recorded run" }).first().click();
  await page.locator("video").evaluate((v: HTMLVideoElement) => { v.currentTime = 9.0; });
  await expect(page.locator(".video-status")).toContainText("O1 observed", { timeout: 15_000 });
  await page.getByRole("button", { name: /SceneTrace/ }).click();
  await page.getByRole("button", { name: "Open recorded run" }).last().click();
  await page.locator("video").evaluate((v: HTMLVideoElement) => { v.currentTime = 9.0; });
  await expect(page.locator(".video-status")).toContainText("not detected", { timeout: 15_000 });
});

test("draw a destination zone and see it during playback", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "pointer drawing is exercised on desktop");
  await page.goto("/");
  await page.getByLabel("Video file").setInputFiles(SAMPLE);
  await page.getByLabel(/I own this footage/).check();
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("heading", { name: "Select the object to follow" })).toBeVisible({ timeout: 60_000 });
  await page.getByLabel("Draw a box on this frame").check();
  const frame = page.locator(".selector-frame");
  await frame.scrollIntoViewIfNeeded();
  const box = (await frame.boundingBox())!;
  const sx = box.width / 960;
  await page.mouse.move(box.x + 509 * sx, box.y + 359 * sx);
  await page.mouse.down();
  await page.mouse.move(box.x + 531 * sx, box.y + 407 * sx, { steps: 4 });
  await page.mouse.up();
  await page.getByLabel(/Draw a zone/).check();
  await frame.scrollIntoViewIfNeeded();
  const zoneBox = (await frame.boundingBox())!;  // checking the box scrolled the page
  for (const [x, y] of [[770, 360], [840, 360], [840, 450], [770, 450]]) {
    await page.mouse.click(zoneBox.x + x * sx, zoneBox.y + y * sx);
  }
  await expect(page.getByText("4 of 16 points.")).toBeVisible();
  await page.getByLabel(/Development fixture/).check();
  await page.getByRole("button", { name: "Analyse this object" }).click();
  await expect(page.locator(".event-list li").first()).toBeVisible({ timeout: 90_000 });
  await expect(page.locator("svg.overlay .zone-overlay")).toHaveCount(1);
  await expect(page.locator("svg.overlay .zone-label")).toContainText("Z1 tray");
});
