/** The public Turnstile site key; empty in local development (no widget). */
export const turnstileSiteKey = (): string => import.meta.env.VITE_TURNSTILE_SITE_KEY ?? "";
