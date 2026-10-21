/**
 * Money form fields: text the user types <-> integer cents. String and BigInt arithmetic only,
 * never floating point (v1 amounts are hundredths of the major unit).
 */

export type MoneyParse = { ok: true; cents: number } | { ok: false; error: string };

// Digits, optional thousands commas in the usual places, optional 1-2 decimals.
const MONEY = /^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?$/;

export function parseMoneyInput(raw: string): MoneyParse {
  const text = raw.trim().replace(/^\$/, "");
  if (!text) return { ok: false, error: "Enter an amount." };
  if (text.startsWith("-")) return { ok: false, error: "The amount can't be negative." };
  if (/\.\d{3,}$/.test(text)) return { ok: false, error: "Use at most 2 decimal places." };
  const match = MONEY.exec(text);
  if (!match) return { ok: false, error: "Enter an amount like 1250 or 1,250.00." };

  const [whole, fraction = ""] = text.replace(/,/g, "").split(".");
  const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0") || "0");
  if (cents > BigInt(Number.MAX_SAFE_INTEGER)) return { ok: false, error: "The amount is too large." };
  return { ok: true, cents: Number(cents) };
}

/** Cents back to an editable string ("1234.50"), exact for every safe integer. */
export function centsToInput(cents: number): string {
  if (!Number.isSafeInteger(cents) || cents < 0) throw new RangeError(`centsToInput expects non-negative integer cents, got ${cents}`);
  const value = BigInt(cents);
  return `${value / 100n}.${(value % 100n).toString().padStart(2, "0")}`;
}
