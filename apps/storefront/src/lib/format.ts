import type { Schemas } from "@delea/api-client";

/**
 * Money display, the same rules as the admin (apps/admin/src/lib/money.ts): amounts are integer
 * hundredths of the major unit, split with integer math; Intl supplies only the symbol and
 * separators.
 */
const formatters = new Map<string, Intl.NumberFormat>();

export function formatMoney(cents: number, currency: string, locale = "en-US"): string {
  if (!Number.isSafeInteger(cents)) throw new RangeError(`formatMoney expects integer cents, got ${cents}`);
  const key = `${locale}|${currency}`;
  let formatter = formatters.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 2 });
    formatters.set(key, formatter);
  }
  const magnitude = BigInt(Math.abs(cents));
  const whole = magnitude / 100n;
  const fraction = (magnitude % 100n).toString().padStart(2, "0");
  const signedWhole = cents < 0 ? -Number(whole) : Number(whole);
  return formatter
    .formatToParts(signedWhole)
    .map((part) => (part.type === "fraction" ? fraction : part.value))
    .join("");
}

/** Whole amounts without the ".00" (the grid shows "$98"), exact cents otherwise. */
export function formatPrice(cents: number, currency: string): string {
  const full = formatMoney(cents, currency);
  return cents % 100 === 0 ? full.replace(/[.,]00(?=\D*$)/, "") : full;
}

const METALS: Record<Schemas["MetalType"], string | null> = {
  gold_14k: "14K gold",
  gold_18k: "18K gold",
  rose_gold: "Rose gold",
  white_gold: "White gold",
  silver_925: "925 silver",
  platinum: "Platinum",
  other: null, // nothing worth showing
};

export const metalLabel = (metal: Schemas["MetalType"]) => METALS[metal];
