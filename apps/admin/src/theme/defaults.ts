/**
 * Neutral platform defaults, used before sign-in and whenever a tenant's brand config is
 * unavailable (for example the CPA role, which can't read it). Tenant brand values are never
 * hardcoded in components: they come from GET /api/v1/settings/brand at runtime.
 * The default colors themselves live in packages/ui tokens.css.
 */
export const DEFAULT_BRAND = {
  businessName: "DELEA",
  logoUrl: null,
} as const;
