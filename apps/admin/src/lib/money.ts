/**
 * Money display. Every amount from the API is an integer number of hundredths of the major unit
 * ("cents"); v1 supports two-decimal currencies only (backend SUPPORTED_CURRENCIES).
 *
 * No floating-point math on amounts: the whole units and the two fraction digits are split with
 * BigInt integer division by 100, and Intl.NumberFormat is used only for the currency symbol,
 * sign, grouping and separators (its fraction digits are replaced with the exact ones).
 */

const formatters = new Map<string, Intl.NumberFormat>();

function formatterFor(currency: string, locale: string): Intl.NumberFormat {
  const key = `${locale}|${currency}`;
  let formatter = formatters.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    formatters.set(key, formatter);
  }
  return formatter;
}

export function formatMoney(cents: number, currency: string, locale = "en-US"): string {
  if (!Number.isSafeInteger(cents)) {
    throw new RangeError(`formatMoney expects integer cents, got ${cents}`);
  }
  const magnitude = BigInt(Math.abs(cents));
  const whole = magnitude / 100n;
  const fraction = (magnitude % 100n).toString().padStart(2, "0");
  // `whole` is at most MAX_SAFE_INTEGER / 100, so Number(whole) is exact. A negative zero keeps
  // the minus sign for amounts between -0.99 and -0.01.
  const signedWhole = cents < 0 ? -Number(whole) : Number(whole);

  return formatterFor(currency, locale)
    .formatToParts(signedWhole)
    .map((part) => (part.type === "fraction" ? fraction : part.value))
    .join("");
}

/** Shown until the tenant's currency has loaded. */
export const FALLBACK_CURRENCY = "USD";
