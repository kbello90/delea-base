import { applyBrandTokens, clearBrandTokens } from "@delea/ui";
import { DEFAULT_BRAND } from "./defaults";

export interface Brand {
  businessName: string;
  logoUrl: string | null;
  /** Null: keep the DELEA default colors from tokens.css. */
  colors: { primary: string; accent: string } | null;
}

export const NEUTRAL_BRAND: Brand = { ...DEFAULT_BRAND, colors: null };

const HEX = /^#[0-9A-Fa-f]{6}$/;

/**
 * Where uploaded logos are served from (the brand-assets bucket): VITE_BRAND_ASSETS_BASE_URL,
 * else the local stack's public URL for the bucket. Plain http is accepted only under it.
 */
export function brandAssetsBase(): string {
  const env = import.meta.env;
  const base = env.VITE_BRAND_ASSETS_BASE_URL || (env.VITE_SUPABASE_URL ? `${env.VITE_SUPABASE_URL}/storage/v1/object/public/brand-assets` : "");
  return base.replace(/\/+$/, "");
}

/** An https URL, or a URL directly under the configured logo storage base. */
export function isAllowedLogoUrl(value: string, storageBase: string = brandAssetsBase()): boolean {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  if (url.protocol === "https:") return true;
  return Boolean(storageBase) && (url.protocol === "http:" || url.protocol === "https:") && value.startsWith(`${storageBase}/`);
}

interface BrandConfigLike {
  business_name?: string | null;
  primary_color?: string | null;
  accent_color?: string | null;
  logo_url?: string | null;
}

/**
 * Validates brand config before it touches the page. Colors are used only if both are
 * #RRGGBB; the logo only if it is https or served from the logo storage base. Anything else
 * falls back to the DELEA defaults.
 */
export function toBrand(config: BrandConfigLike, storageBase: string = brandAssetsBase()): Brand {
  const { primary_color: primary, accent_color: accent, logo_url: logo, business_name: name } = config;
  return {
    businessName: name?.trim() || DEFAULT_BRAND.businessName,
    logoUrl: logo && isAllowedLogoUrl(logo, storageBase) ? logo : null,
    colors: primary && accent && HEX.test(primary) && HEX.test(accent) ? { primary, accent } : null,
  };
}

/** Sets (or clears) the --brand* CSS variables on the document root. */
export function applyBrandColors(colors: Brand["colors"], el: HTMLElement = document.documentElement): void {
  if (!colors) {
    clearBrandTokens(el);
    return;
  }
  applyBrandTokens(
    {
      brand: colors.primary,
      brandDeep: `color-mix(in srgb, ${colors.primary} 72%, black)`,
      brandGold: colors.accent,
      // Mixed with --surface so it stays soft in both light and dark themes.
      brandGoldSoft: `color-mix(in srgb, ${colors.accent} 28%, var(--surface))`,
    },
    el,
  );
}
