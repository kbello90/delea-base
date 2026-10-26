/**
 * One checkout key per checkout attempt (TRD §6.12): the server creates at most one order per
 * key, and answers a repeat of the same request with that order. So a retry or a double click
 * reuses the key. The key changes only when the order itself changes (for example after the
 * shopper accepts a new total), because the server refuses a different request with a used key.
 */
export interface Attempt {
  key: string;
  fingerprint: string;
}

const NOT_THE_ORDER = new Set(["turnstile_token", "website", "elapsed_ms", "checkout_key"]);

/** The parts of the order that the key stands for (not the bot checks or the Turnstile token). */
export function orderFingerprint(body: Record<string, unknown>): string {
  const order = Object.fromEntries(Object.entries(body).filter(([key]) => !NOT_THE_ORDER.has(key)));
  return stableJson(order);
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => a.localeCompare(b));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stableJson(v)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function nextAttempt(previous: Attempt | null, fingerprint: string, newKey: () => string = () => crypto.randomUUID()): Attempt {
  if (previous && previous.fingerprint === fingerprint) return previous;
  return { key: newKey(), fingerprint };
}
