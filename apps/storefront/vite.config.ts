import react from "@vitejs/plugin-react";
import type { ServerResponse } from "node:http";
import { defineConfig, loadEnv, type Plugin } from "vite";
import { ADMIN_ORIGIN_ENV, frameAncestors } from "./src/lib/frameAncestors";

/** The frame-ancestors header on every response of the dev and preview servers (hosted: middleware.ts). */
function frameHeader(value: string): Plugin {
  const set = (_req: unknown, res: ServerResponse, next: () => void) => {
    res.setHeader("Content-Security-Policy", value);
    next();
  };
  return {
    name: "delea-frame-ancestors",
    configureServer: (server) => void server.middlewares.use(set),
    configurePreviewServer: (server) => void server.middlewares.use(set),
  };
}

export default defineConfig(({ mode }) => {
  // ADMIN_ORIGIN from the environment or apps/storefront/.env.local (never in code).
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react(), frameHeader(frameAncestors(env[ADMIN_ORIGIN_ENV]))],
    server: { port: 5174, strictPort: true },
    preview: { port: 5174, strictPort: true },
  };
});
