// Capture README screenshots from a running SceneTrace server (default http://127.0.0.1:8765).
// Usage: node scripts/capture-screens.mjs <owned-analysis-id>   (run from client/)
import { chromium } from "@playwright/test";

const base = process.env.SCENETRACE_URL ?? "http://127.0.0.1:8765";
const out = "../docs/media";
const owned = process.argv[2];
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome" });

async function demoIds(page) {
  return page.evaluate(async () => (await (await fetch("/api/v1/demos")).json()).map((d) => d.analysis_id));
}
async function settle(page, ms = 1200) { await page.waitForTimeout(ms); }

const desk = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await desk.goto(base);
await desk.waitForSelector(".cards .card");
await desk.screenshot({ path: `${out}/home-desktop.png` });
const [pickup, hidden] = await demoIds(desk);

await desk.goto(`${base}/#/analysis/${pickup}`);
await desk.waitForSelector(".answer-card");
await desk.locator("video").evaluate((v) => { v.currentTime = 13.8; });
await settle(desk, 1800);
await desk.screenshot({ path: `${out}/workspace-desktop.png` });
await desk.locator(".event.status-rejected summary").click();
await settle(desk, 400);
await desk.locator(".history").screenshot({ path: `${out}/pass-by-why-not.png` });
await desk.getByRole("button", { name: "Open evidence for E3" }).first().click();
await settle(desk, 1500);
await desk.screenshot({ path: `${out}/evidence-drawer-desktop.png` });
await desk.keyboard.press("Escape");

await desk.goto(`${base}/#/analysis/${hidden}`);
await desk.waitForSelector(".answer-card");
await desk.locator(".event.status-plausible summary").first().click();
await desk.locator("video").evaluate((v) => { v.currentTime = 9.0; });
await settle(desk, 1500);
await desk.screenshot({ path: `${out}/occlusion-desktop.png` });

if (owned) {
  await desk.goto(`${base}/#/analysis/${owned}`);
  await desk.waitForSelector(".integrity");
  const create = desk.getByRole("button", { name: "Create sealed export" });
  if (await create.isVisible()) await create.click();
  await desk.getByRole("button", { name: "Verify against the server registry" }).click({ timeout: 60_000 });
  await desk.waitForSelector(".verify-result");
  await settle(desk, 500);
  await desk.locator(".integrity").screenshot({ path: `${out}/integrity-anchored.png` });
  await desk.locator("#expected").fill("0".repeat(64));
  await desk.locator(".verify-own button").click();
  await desk.waitForSelector(".verify-result.failed");
  await desk.locator(".integrity").screenshot({ path: `${out}/integrity-wrong-anchor.png` });
}

const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
await phone.goto(`${base}/#/analysis/${pickup}`);
await phone.waitForSelector(".event-list li");
await phone.locator("video").evaluate((v) => { v.currentTime = 13.8; });
await settle(phone, 1500);
await phone.screenshot({ path: `${out}/workspace-phone.png` });
await phone.getByRole("tab", { name: "Ask" }).click();
await phone.locator(".area-tabs").evaluate((el) => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 64));
await settle(phone, 600);
await phone.screenshot({ path: `${out}/answer-phone.png` });
await browser.close();
console.log("screenshots written to docs/media");
