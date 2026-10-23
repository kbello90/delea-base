/**
 * Percentages typed by people <-> integer basis points (1% = 100 bps), exactly: the decimal
 * string is split into whole and fraction digits, never multiplied as a floating-point number
 * (8.25 * 100 is 825.0000000000001 and 1.15 * 100 is 114.99999999999999).
 */

export type PercentParse = { ok: true; bps: number } | { ok: false; error: string };

const PERCENT = /^\d{1,3}(?:\.\d{1,2})?$/;

/** "8.25" -> 825, "7.1" -> 710, "0.07" -> 7, "100" -> 10000. At most 2 decimals, 0 to 100. */
export function parsePercent(raw: string, what = "The rate"): PercentParse {
  const text = raw.trim().replace(/\s*%$/, "");
  if (!text) return { ok: false, error: `Enter ${what.toLowerCase()} as a percentage, like 8.25.` };
  if (text.startsWith("-")) return { ok: false, error: `${what} can't be negative.` };
  if (/\.\d{3,}$/.test(text)) return { ok: false, error: "Use at most 2 decimal places." };
  if (!PERCENT.test(text)) return { ok: false, error: `Enter ${what.toLowerCase()} as a percentage, like 8.25.` };
  const [whole, fraction = ""] = text.split(".");
  const bps = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (bps > 10_000) return { ok: false, error: `${what} can't be more than 100%.` };
  return { ok: true, bps };
}

/** 825 -> "8.25", 710 -> "7.1", 7 -> "0.07", 800 -> "8". Integer arithmetic only. */
export function formatPercent(bps: number): string {
  if (!Number.isSafeInteger(bps) || bps < 0) throw new RangeError(`formatPercent expects non-negative integer bps, got ${bps}`);
  const whole = Math.trunc(bps / 100);
  const fraction = String(bps % 100).padStart(2, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : String(whole);
}
