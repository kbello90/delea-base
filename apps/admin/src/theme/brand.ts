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

function isHttpsUrl(value: string): boolean {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

interface BrandConfigLike {
  business_name?: string | null;
  primary_color?: string | null;
  accent_color?: string | null;
  logo_url?: string | null;
}

/**
 * Validates brand config before it touches the page. Colors are used only if both are
 * #RRGGBB; the logo only if it is an https URL. Anything else falls back to the DELEA defaults.
 */
export function toBrand(config: BrandConfigLike): Brand {
  const { primary_color: primary, accent_color: accent, logo_url: logo, business_name: name } = config;
  return {
    businessName: name?.trim() || DEFAULT_BRAND.businessName,
    logoUrl: logo && isHttpsUrl(logo) ? logo : null,
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
