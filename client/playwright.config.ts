import { defineConfig } from "@playwright/test";

// Browser tests run against the real server in fixture mode on a throwaway data directory.
// Recorded demonstrations are replayed into it at startup; no provider is ever called.
const port = 8799;
export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}`, channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome", trace: "off" },
  webServer: {
    command: `cd .. && rm -rf .local/e2e-data && SCENETRACE_DATA_DIR=.local/e2e-data SCENETRACE_MODE=fixture MODEL_API_KEY= SCENETRACE_DEMO_ACCESS_CODE= SCENETRACE_ENABLE_SECONDARY_REVIEW=false PYTHONPATH=. .venv/bin/python -m scenetrace serve --port ${port} --import-demos`,
    url: `http://127.0.0.1:${port}/api/v1/ready`,
    timeout: 180_000,
    reuseExistingServer: false,
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
    { name: "phone", use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } },
  ],
});
