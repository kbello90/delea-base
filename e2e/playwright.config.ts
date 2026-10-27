/**
 * One end-to-end run against the LOCAL stack (never CI, never a hosted project):
 * browse, personalize, check out, confirmation, tracking, then the order in the admin.
 *
 *   npx supabase@2.121.0 start          # the local stack, with backend/.env written
 *   pnpm --filter @delea/e2e e2e
 *
 * Playwright starts its own API (port 8001), storefront (5175) and admin (5176), so it never
 * touches dev servers on 8000/5173/5174. Turnstile uses Cloudflare's published test keys
 * (they always pass; they are not secrets). The shop is a throwaway demo tenant made by
 * backend/scripts/e2e_shop.py and deleted afterwards. The browser is the installed Microsoft
 * Edge (channel "msedge"), so nothing is downloaded.
 */
import { defineConfig } from "@playwright/test";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("..", import.meta.url));
// Absolute and quoted: the repository path may contain spaces.
const python = `"${process.platform === "win32" ? join(repo, "backend", ".venv", "Scripts", "python.exe") : join(repo, "backend", ".venv", "bin", "python")}"`;

// Cloudflare's documented Turnstile test keys: always pass (https://developers.cloudflare.com/turnstile/troubleshooting/testing/).
export const TURNSTILE_TEST_SITE_KEY = "1x00000000000000000000AA";
const TURNSTILE_TEST_SECRET = "1x0000000000000000000000000000000AA";

export const PORTS = { api: 8001, storefront: 5175, admin: 5176 };

export default defineConfig({
  testDir: "./tests",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  globalSetup: "./global-setup.ts",
  globalTeardown: "./global-teardown.ts",
  outputDir: "./test-results",
  use: {
    channel: "msedge",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `${python} -m uvicorn app.main:app --port ${PORTS.api}${process.platform === "win32" ? " --loop asyncio:SelectorEventLoop" : ""}`,
      cwd: join(repo, "backend"),
      url: `http://127.0.0.1:${PORTS.api}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ENVIRONMENT: "local",
        TURNSTILE_SECRET_KEY: TURNSTILE_TEST_SECRET,
        CORS_ORIGINS: `http://localhost:${PORTS.admin}`,
      },
    },
    {
      command: `pnpm --filter @delea/storefront exec vite --port ${PORTS.storefront} --strictPort`,
      cwd: repo,
      url: `http://localhost:${PORTS.storefront}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { VITE_API_BASE_URL: `http://localhost:${PORTS.api}`, VITE_TURNSTILE_SITE_KEY: TURNSTILE_TEST_SITE_KEY },
    },
    {
      command: `pnpm --filter @delea/admin exec vite --port ${PORTS.admin} --strictPort`,
      cwd: repo,
      url: `http://localhost:${PORTS.admin}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { VITE_API_URL: `http://localhost:${PORTS.api}` },
    },
  ],
});
