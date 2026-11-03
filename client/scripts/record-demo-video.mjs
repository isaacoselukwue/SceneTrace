import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const base = process.env.SCENETRACE_URL ?? "http://127.0.0.1:8765";
const [outDir, transcriptPath, clipPath] = process.argv.slice(2);
const clip = path.resolve(clipPath);
const transcript = JSON.parse(fs.readFileSync(transcriptPath, "utf8"));
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome" });
const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, recordVideo: { dir: outDir, size: { width: 1280, height: 720 } } });
const page = await context.newPage();
const t0 = Date.now();
const markers = [];
const now = () => (Date.now() - t0) / 1000;
const wait = (ms) => page.waitForTimeout(ms);

const interFile = fs.readdirSync("dist/assets").find((f) => /^inter-latin-wght-normal-.*\.woff2$/.test(f));
const interFace = interFile
  ? `@font-face{font-family:"Inter Card";font-weight:100 900;src:url(data:font/woff2;base64,${fs.readFileSync(path.join("dist/assets", interFile)).toString("base64")}) format("woff2")}`
  : "";
const monoFile = fs.readdirSync("dist/assets").find((f) => /^jetbrains-mono-latin-wght-normal-.*\.woff2$/.test(f));
const monoFace = monoFile
  ? `@font-face{font-family:"Mono Card";font-weight:100 900;src:url(data:font/woff2;base64,${fs.readFileSync(path.join("dist/assets", monoFile)).toString("base64")}) format("woff2")}`
  : "";
const CARD_CSS = `${interFace} ${monoFace} body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#f7f6f2;color:#1e293b;
  font-family:"Inter Card",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif} .card{max-width:1000px;padding:48px} h1{font-size:64px;margin:0 0 8px}
  h2{font-size:30px;margin:0 0 16px;color:#0f766e;font-weight:600} p{font-size:24px;line-height:1.45;margin:10px 0} .small{font-size:18px;color:#5b6575}
  table{font-size:22px;border-collapse:collapse;margin:12px 0} td{padding:6px 18px 6px 0} .mono{font-family:"Mono Card",ui-monospace,Menlo,monospace;font-size:20px}`;

async function card(html, ms) {
  await page.setContent(`<!doctype html><html><head><style>${CARD_CSS}</style></head><body><div class="card">${html}</div></body></html>`);
  await wait(ms);
}

async function caption(text) {
  markers.push({ caption: text, at: now() });
  await page.evaluate((t) => {
    let el = document.getElementById("demo-caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "demo-caption";
      el.style.cssText = "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);max-width:1120px;width:calc(100% - 64px);" +
        "background:rgba(15,23,42,.92);color:#fff;font:500 21px/1.4 system-ui,sans-serif;padding:12px 20px;border-radius:10px;" +
        "z-index:9999;box-shadow:0 4px 18px rgba(0,0,0,.25);pointer-events:none";
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
}

async function open(hash) {
  await page.goto(`${base}/${hash}`);
  await page.waitForLoadState("networkidle");
}

await card(`<h1>SceneTrace</h1><h2>Follow the object. Check the evidence.</h2>
  <p>Created by <strong>Isaac Oselukwue</strong></p>
  <p>Built on Meta Model API: SAM 3.1, Muse Spark 1.3 and Muse Wonder 1.0</p>
  <p class="small">A real screen recording of the running application. The office clip was filmed with my colleagues' permission; the bench clips
  are drawn test animations. Waiting time during live model calls is shown at 2x speed and labelled.</p>`, 8000);
await card(`<h2>How it works</h2>
  <p>1. SAM 3.1 tracks the selected object and the people near it.</p>
  <p>2. Changes in the object's motion become candidates. Muse Spark assesses each one from original frames.</p>
  <p>3. Deterministic rules decide what is supported. Closeness is never treated as contact.</p>
  <p>4. Questions are answered only with claims the server validates, each linked to its evidence.</p>`, 10000);

await page.setContent(`<!doctype html><html><head><style>${interFace} ${monoFace}
  body{margin:0;background:#0b1220;color:#d1fae5;font:18px/1.6 "Mono Card",ui-monospace,Menlo,monospace;padding:48px;overflow-wrap:anywhere}
  .label{color:#94a3b8;font-family:"Inter Card",system-ui,sans-serif;font-size:18px;margin-bottom:20px} .cmd{color:#fff}</style></head>
  <body><div class="label">Launching the recorded demonstrations. Real terminal output captured from this build, replayed at 2x speed.</div><div id="term"></div></body></html>`);
let shown = 0;
for (const line of transcript) {
  await wait(Math.max(0, (line.t - shown) * 500));
  shown = line.t;
  await page.evaluate((l) => {
    const div = document.createElement("div");
    div.textContent = l;
    if (l.startsWith("$")) div.className = "cmd";
    document.getElementById("term").appendChild(div);
  }, line.text);
}
await wait(2000);

await open("#/");
await caption("The home page lists recorded investigations, which replay stored live runs without new model calls. Uploads stay private to the owner.");
await wait(4500);

await caption("A real eight-second clip, filmed in our office with my colleagues' permission: a stain-remover spray bottle on a desk.");
await page.getByRole("button", { name: "Analyse your own clip" }).click();
await wait(1200);
await page.getByLabel("Video file").setInputFiles(clip);
await page.getByLabel(/I own this footage/).check();
await wait(1800);
await page.getByRole("button", { name: "Upload" }).click();
await page.getByRole("heading", { name: "Select the object to follow" }).waitFor({ timeout: 60000 });
await page.getByLabel("Frame time").evaluate((input) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "1000");
  input.dispatchEvent(new Event("input", { bubbles: true }));
});
await caption("Pick a frame where the object is clear and describe it in a few words. SAM 3.1 finds it on that frame.");
await wait(1500);
await page.getByLabel(/Short noun phrase/).pressSequentially("spray bottle", { delay: 110 });
await wait(1500);
await page.getByRole("radio", { name: /Live analysis/ }).check();
await caption("Live mode calls Meta Model API. Every paid call is reserved against the cost caps before it is sent.");
await wait(3500);

await page.getByRole("button", { name: "Analyse this object" }).click();
const analysisWait = { start: now(), speed: 2, label: "live analysis" };
markers.push(analysisWait);
await caption("Live analysis (shown at 2x speed): SAM 3.1 tracks the bottle and the people, then Muse Spark checks each candidate against the original frames.");
await page.locator(".event-list li").first().waitFor({ timeout: 240000 });
await page.locator(".answer").waitFor({ timeout: 240000 });
analysisWait.end = now();
const analysisUrl = page.url();

await page.locator(".event.status-rejected summary").first().click();
await caption("I walked past first and the bottle stayed put, so that candidate is not supported: being close is not touching.");
await wait(6500);
await page.locator(".event.status-plausible summary").first().click();
await page.locator(".event.status-plausible").first().evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
await caption("Then the bottle vanished behind us. The lift is hidden, but the bottle was next seen moving with P2 and no one else, so this is a possible pick-up by P2, never a supported one.");
await wait(8500);
await page.locator("video").evaluate((v) => { v.currentTime = 3.5; });
await page.locator(".stage").evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
await caption("The overlays show only what SAM tracked: here, at 3.5 seconds, the bottle beside P2 as they walk away.");
await wait(6000);

await page.locator(".composer").evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
await wait(900);
await page.locator("#question").fill("Who took the spray bottle?");
await wait(1200);
await page.getByRole("button", { name: "Investigate" }).click();
const questionWait = { start: now(), speed: 2, label: "live question" };
markers.push(questionWait);
await caption("Live question (shown at 2x speed): the investigator reads the stored history and may only submit structured claims.");
await page.locator(".answer-card .citation").first().waitFor({ timeout: 180000 });
questionWait.end = now();
await page.locator(".answer-card").first().evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
await caption("The server checked every claim and wrote the sentences: P2 possibly picked it up, and the footage does not establish who did.");
await wait(8000);
await page.locator(".answer-card .citation", { hasText: "E3" }).first().click();
await page.locator(".drawer figure img").first().waitFor();
await caption("Each citation opens its evidence: the original frames before, during and after the hidden change, with frame numbers and digests.");
await wait(3500);
await page.locator(".drawer figure").nth(2).evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
await wait(3500);
await page.keyboard.press("Escape");

const demos = await page.evaluate(async () => (await (await fetch("/api/v1/demos")).json()));
const pickup = demos.find((d) => d.title === "Bench pickup and pass-by");
const hidden = demos.find((d) => d.title === "Hidden removal");
await open(`#/analysis/${pickup.analysis_id}`);
await page.locator("video").evaluate((v) => { v.currentTime = 13.8; });
await caption("For contrast, a recorded run on a drawn test clip: when the lift is on camera, the pick-up by P2 is supported.");
await wait(6500);
await open(`#/analysis/${hidden.analysis_id}`);
await page.locator(".event.status-plausible summary").first().click();
await caption("And when an object simply vanishes behind someone and is never seen again, SceneTrace names no one.");
await wait(6000);
await page.getByRole("button", { name: /Where was the bottle last seen/ }).click();
await page.locator(".answer-card .blocked summary").click();
await page.locator(".answer-card").evaluate((el) => el.scrollIntoView({ block: "center" }));
await caption("Here Muse Wonder reviewed the answer against the frames and withheld a claim it could not verify. Review can block a claim, never promote one.");
await wait(7500);

await page.goto(analysisUrl);
await page.waitForLoadState("networkidle");
await page.locator(".integrity").scrollIntoViewIfNeeded();
await caption("Seal the evidence: a content-addressed bundle whose expected head is kept outside the bundle.");
await page.getByRole("button", { name: "Create sealed export" }).click();
await page.getByRole("button", { name: "Verify against the server registry" }).click({ timeout: 60000 });
await page.locator(".verify-result").waitFor();
await page.locator(".verify-result").evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
await wait(4000);
await page.locator("#expected").fill("0".repeat(64));
await page.locator(".verify-own button").click();
await page.locator(".verify-result.failed").waitFor();
await page.locator(".verify-result").evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
await caption("Checked against a different head, verification fails. Integrity shows the evidence is unchanged, not that the interpretation is true.");
await wait(5500);

await card(`<h2>Measured so far</h2>
  <table>
  <tr><td>Planted faults blocked by the validator</td><td class="mono">30 / 30, 0 / 10 clean claims blocked</td></tr>
  <tr><td>Muse Wonder reviewer alone</td><td class="mono">22 / 30, 3 / 10 clean claims blocked</td></tr>
  <tr><td>Synthetic smoke check, full system</td><td class="mono">TP 1, FP 0, FN 0</td></tr>
  <tr><td>Synthetic smoke check, geometry only</td><td class="mono">TP 1, FP 1, FN 0</td></tr>
  <tr><td>Muse Spark on the whole video, as a baseline</td><td class="mono">TP 1, FP 0, FN 0, no evidence links</td></tr>
  <tr><td>Real-video accuracy</td><td class="mono">not yet measured (0 labelled clips)</td></tr>
  </table>
  <p class="small">Drawn test clips are smoke checks, not benchmarks. Live runs: cold analysis median 33 s and about USD 0.09; questions median 18 s.
  The token count endpoint leaves out video tokens (384 counted, 5,311 billed), so video reservations add 600 tokens per second.</p>
  <p><strong>SceneTrace</strong>, created by Isaac Oselukwue. github.com/isaacoselukwue/SceneTrace</p>`, 16000);

markers.push({ total: now() });
await context.close();
await browser.close();
const video = fs.readdirSync(outDir).find((f) => f.endsWith(".webm") && f !== "raw.webm");
fs.renameSync(path.join(outDir, video), path.join(outDir, "raw.webm"));
fs.writeFileSync(path.join(outDir, "markers.json"), JSON.stringify(markers, null, 1));
console.log(JSON.stringify(markers));
