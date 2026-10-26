import { applyBrandTokens, clearBrandTokens } from "@delea/ui";
import type { Store } from "./api";

const HEX = /^#[0-9A-Fa-f]{6}$/;
// The second hero button's link, checked again here although the API and the database already
// refuse anything else: https:// with a plain host, or a tel: number.
const HTTPS_LINK = /^https:\/\/[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:[0-9]{1,5})?([/?#][^\s<>"]*)?$/i;
const TEL_LINK = /^tel:\+?[0-9][0-9().-]{2,29}$/;

export function isSafeHeroLink(value: string): boolean {
  return value.length <= 500 && (HTTPS_LINK.test(value) || TEL_LINK.test(value));
}

/** Only http(s) image URLs reach an <img>. */
export function isSafeImageUrl(value: string | null | undefined): value is string {
  if (!value) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

/**
 * The shop's look on the document root: brand colours as the --brand* tokens (only valid
 * #RRGGBB values), and the font and corner presets as data attributes (styles/storefront.css).
 */
export function applyStoreTheme(store: Store, el: HTMLElement = document.documentElement): void {
  if (HEX.test(store.primary_color) && HEX.test(store.accent_color)) {
    applyBrandTokens(
      {
        brand: store.primary_color,
        brandDeep: `color-mix(in srgb, ${store.primary_color} 72%, black)`,
        brandGold: store.accent_color,
        brandGoldSoft: `color-mix(in srgb, ${store.accent_color} 28%, white)`,
      },
      el,
    );
  } else {
    clearBrandTokens(el);
  }
  el.dataset.font = store.font_preset;
  el.dataset.corners = store.corner_style;
}

export function clearStoreTheme(el: HTMLElement = document.documentElement): void {
  clearBrandTokens(el);
  delete el.dataset.font;
  delete el.dataset.corners;
}
