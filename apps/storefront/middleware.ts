// Vercel Routing Middleware (hosted only; local and e2e get the same header from vite.config.ts):
// the storefront's pages may be framed by itself and the admin (ADMIN_ORIGIN) only.
import { next } from "@vercel/functions";
import { ADMIN_ORIGIN_ENV, frameAncestors } from "./src/lib/frameAncestors";

// Pages only: built files under /assets are never framed, so they skip the middleware.
export const config = { matcher: ["/((?!assets/).*)"] };

export default function middleware(): Response {
  return next({ headers: { "Content-Security-Policy": frameAncestors(process.env[ADMIN_ORIGIN_ENV]) } });
}
