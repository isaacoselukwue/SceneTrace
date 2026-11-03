import { expect, test } from "@playwright/test";
import path from "node:path";

const SAMPLE = path.resolve(new URL("../../samples/synthetic/synthetic_bench_pickup.mp4", import.meta.url).pathname);

test.beforeEach(async ({ page }) => {
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
  await page.getByLabel("Draw a box").check();
  const frame = page.locator(".selector-frame");
  const box = (await frame.boundingBox())!;
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
  await expect(page.locator(".verify-result")).toContainText("Anchored to the server registry");
});

test("phone: draw the box by touch, analyse, ask, cite and verify without sideways scrolling", async ({ page }, info) => {
  test.skip(info.project.name !== "phone", "touch input is exercised on the phone project");
  const width = page.viewportSize()!.width;
  const fitsScreen = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  await page.goto("/");
  await fitsScreen();
  await page.getByLabel("Video file").setInputFiles(SAMPLE);
  await page.getByLabel(/I own this footage/).check();
  await page.getByRole("button", { name: "Upload" }).click();
  const heading = page.getByRole("heading", { name: "Select the object to follow" });
  await expect(heading).toBeVisible({ timeout: 60_000 });
  const frame = page.locator(".selector-frame");
  const headingBottom = await heading.evaluate((el) => el.getBoundingClientRect().bottom);
  expect(headingBottom).toBeLessThan(await frame.evaluate((el) => el.getBoundingClientRect().top));
  await expect(page.getByRole("button", { name: "Analyse this object" })).toBeInViewport();
  await page.getByLabel("Draw a box").check();
  await frame.evaluate((el) => el.scrollIntoView({ block: "center" }));
  const box = (await frame.boundingBox())!;
  const sx = box.width / 960;
  const cdp = await page.context().newCDPSession(page);
  const touch = (type: "touchStart" | "touchMove" | "touchEnd", x: number, y: number) => cdp.send("Input.dispatchTouchEvent", {
    type, touchPoints: type === "touchEnd" ? [] : [{ x: box.x + x * sx, y: box.y + y * sx }],
  });
  const scrollBefore = await page.evaluate(() => window.scrollY);
  await touch("touchStart", 505, 355);
  for (const step of [1, 2, 3, 4, 5]) await touch("touchMove", 505 + step * 6, 355 + step * 11);
  await touch("touchEnd", 0, 0);
  await expect(page.getByText(/^Box \d+, \d+, \d+, \d+ in proxy pixels/)).toBeVisible();
  expect(await page.evaluate(() => window.scrollY)).toBe(scrollBefore);
  await fitsScreen();
  await page.getByLabel(/Development fixture/).check();
  await page.getByRole("button", { name: "Analyse this object" }).click();
  await expect(page.locator(".event-list li").first()).toBeVisible({ timeout: 90_000 });
  await fitsScreen();
  const touchTargetHeights = await page.locator(".time-chip, .evidence-link").evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height));
  expect(Math.min(...touchTargetHeights)).toBeGreaterThanOrEqual(40);
  const tabs = page.locator(".tabs.mobile-only");
  await tabs.getByRole("tab", { name: "Ask" }).click();
  await page.getByRole("button", { name: "Who picked it up?" }).click();
  const citation = page.locator(".citation").first();
  await expect(citation).toBeVisible({ timeout: 60_000 });
  await fitsScreen();
  await citation.click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.locator("figure img").first()).toBeVisible();
  await expect.poll(async () => {
    const sheet = (await drawer.boundingBox())!;
    return sheet.x >= 0 && sheet.x + sheet.width <= width;
  }).toBe(true);
  await drawer.getByRole("button", { name: "Close evidence" }).click();
  await tabs.getByRole("tab", { name: "Integrity" }).click();
  await page.getByRole("button", { name: "Create sealed export" }).click();
  await page.getByRole("button", { name: "Verify against the server registry" }).click({ timeout: 60_000 });
  await expect(page.locator(".verify-result")).toContainText("Integrity valid");
  await fitsScreen();
});

test("tracks appear when an analysis finishes while its page is open", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "checked once on desktop");
  let armed = false;
  let released = false;
  await page.route(/\/api\/v1\/analyses\/ana_[a-z0-9]+$/, async (route) => {
    if (!armed || released) return route.continue();
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), status: "running" } });
  });
  await page.route(/\/api\/v1\/analyses\/ana_[a-z0-9]+\/observations/, async (route) => {
    if (!armed || released) return route.continue();
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), observations: [] } });
  });
  await page.route(/\/api\/v1\/jobs\/job_[a-z0-9]+\/events/, async (route) => {
    if (armed && !released) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      released = true;
    }
    await route.continue();
  });
  await page.goto("/");
  await page.getByLabel("Video file").setInputFiles(SAMPLE);
  await page.getByLabel(/I own this footage/).check();
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("heading", { name: "Select the object to follow" })).toBeVisible({ timeout: 60_000 });
  await page.getByLabel("Draw a box").check();
  const frame = page.locator(".selector-frame");
  const box = (await frame.boundingBox())!;
  const sx = box.width / 960;
  await page.mouse.move(box.x + 509 * sx, box.y + 359 * sx);
  await page.mouse.down();
  await page.mouse.move(box.x + 531 * sx, box.y + 407 * sx, { steps: 4 });
  await page.mouse.up();
  await page.getByLabel(/Development fixture/).check();
  armed = true;
  await page.getByRole("button", { name: "Analyse this object" }).click();
  await expect.poll(() => released, { timeout: 30_000 }).toBe(true);
  await expect(page.locator(".progress-panel")).toHaveCount(0, { timeout: 30_000 });
  await page.locator("video").evaluate((v: HTMLVideoElement) => { v.currentTime = 3.0; });
  await expect(page.locator(".video-status")).toContainText("O1 observed", { timeout: 15_000 });
  await expect(page.locator("svg.overlay rect").first()).toBeAttached();
});

test("recorded demonstration replays with its labels and original cost", async ({ page }) => {
  await page.goto("/");
  const demoTitle = (await page.locator(".demo-card h3").first().textContent())!;
  await page.getByRole("button", { name: "Open recorded run" }).first().click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(demoTitle);
  await expect(page.locator(".banner-recorded")).toContainText("Replayed without new model calls");
  const trackLabel = page.locator("svg.overlay .overlay-label").first();
  await expect(trackLabel).toBeVisible();
  expect((await trackLabel.boundingBox())!.height).toBeGreaterThanOrEqual(10);
  await expect(page.locator(".banner-synthetic")).toBeVisible();
  const tabs = page.locator(".tabs.mobile-only");
  if (await tabs.isVisible()) await tabs.getByRole("tab", { name: "History" }).click();
  await expect(page.locator(".event-list li").first()).toBeVisible();
  if (await tabs.isVisible()) await tabs.getByRole("tab", { name: "Ask" }).click();
  await expect(page.locator(".answer-card").first()).toBeVisible();
  await expect(page.getByText(/recorded demonstration replays its recorded questions/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(page.viewportSize()!.width);
});

test("cards that share a row share their top and bottom edges", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "rows only exist on wide layouts");
  const edges = (selector: string) => page.locator(selector).evaluateAll((elements) => {
    const boxes = elements.map((element) => element.getBoundingClientRect());
    return { top: Math.round(Math.min(...boxes.map((box) => box.top))), bottom: Math.round(Math.max(...boxes.map((box) => box.bottom))) };
  });
  await page.goto("/");
  await expect(page.locator(".upload-area > .clips")).toBeVisible();
  expect(await edges(".upload-area > .upload")).toEqual(await edges(".upload-area > .clips"));
  const demoButtons = await page.getByRole("button", { name: "Open recorded run" }).evaluateAll((buttons) => buttons.map((button) => Math.round(button.getBoundingClientRect().top)));
  expect(new Set(demoButtons).size).toBe(1);
  for (const index of [0, 1]) {
    await page.goto("/");
    await page.getByRole("button", { name: "Open recorded run" }).nth(index).click();
    await expect(page.locator(".answer-card")).toBeVisible();
    expect(await edges(".area-video > .video-panel")).toEqual(await edges(".area-history > .panel"));
    expect(await edges(".area-answer > .panel")).toEqual(await edges(".area-integrity > .panel, .area-usage > .panel"));
    const history = page.locator(".area-history .history-scroll");
    expect(await history.evaluate((element) => element.scrollHeight <= element.clientHeight)).toBe(true);
  }
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
  await page.getByLabel("Draw a box").check();
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
  const zoneBox = (await frame.boundingBox())!;
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
