/**
 * Who may show the storefront inside a frame (Website slice): the storefront itself and the
 * admin, for the Website page's live preview. Nobody else, so another site can't frame the
 * shop to trick a shopper (clickjacking).
 *
 * The admin's origin comes from the `ADMIN_ORIGIN` environment setting, never from code. A
 * missing or invalid value fails closed: only the storefront itself may frame it, and the
 * Website page shows that the preview couldn't load.
 *
 * Used by the Vite dev and preview servers (vite.config.ts: local and e2e) and by the Vercel
 * middleware (middleware.ts: hosted).
 */
export const ADMIN_ORIGIN_ENV = "ADMIN_ORIGIN";

const LOCAL_HOST = /^(localhost|127\.0\.0\.1|[a-z0-9-]+(\.[a-z0-9-]+)*\.localhost)$/;

/**
 * The one origin in `value`, or null. An origin is scheme://host[:port] and nothing else:
 * https, or http for a local host only; no path, wildcard, list or credentials.
 */
export function adminOrigin(value: string | undefined): string | null {
  const text = value?.trim() ?? "";
  if (!/^https?:\/\/[^\s/?#*@,;'"]+$/i.test(text)) return null;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol === "http:" && !LOCAL_HOST.test(url.hostname)) return null;
  return url.origin;
}

/** The Content-Security-Policy value: frame-ancestors only. */
export function frameAncestors(value: string | undefined): string {
  const origin = adminOrigin(value);
  return origin ? `frame-ancestors 'self' ${origin}` : "frame-ancestors 'self'";
}

/** Sent to the parent window once the storefront runs inside a frame: the preview loaded. */
export const READY_MESSAGE = "delea:storefront-ready";
