/**
 * The shopper's bag: what they want and how many, never a price (the server prices every
 * cart, TRD §6.12). Kept in localStorage per shop host, so two shops never share a bag; a
 * blocked or broken storage just means an empty bag.
 */

export const MAX_LINES = 50;
export const MAX_QTY = 99;

export interface BagLine {
  /** product + answers: the same piece with different answers is a separate line */
  key: string;
  productId: string;
  qty: number;
  /** personalization field id -> answer */
  personalization: Record<string, string>;
}

export function lineKey(productId: string, personalization: Record<string, string>): string {
  const answers = Object.entries(personalization)
    .filter(([, value]) => value.trim())
    .sort(([a], [b]) => a.localeCompare(b));
  return `${productId}|${JSON.stringify(answers)}`;
}

const storageKey = () => `delea-bag:v1:${typeof location === "undefined" ? "" : location.host}`;

function isLine(value: unknown): value is BagLine {
  if (!value || typeof value !== "object") return false;
  const line = value as Record<string, unknown>;
  return (
    typeof line.productId === "string" &&
    Number.isInteger(line.qty) &&
    (line.qty as number) >= 1 &&
    (line.qty as number) <= MAX_QTY &&
    !!line.personalization &&
    typeof line.personalization === "object" &&
    Object.values(line.personalization as object).every((v) => typeof v === "string")
  );
}

export function loadBag(): BagLine[] {
  try {
    const raw = localStorage.getItem(storageKey());
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(isLine)
      .slice(0, MAX_LINES)
      .map((line) => ({ ...line, key: lineKey(line.productId, line.personalization) }));
  } catch {
    return [];
  }
}

export function saveBag(lines: BagLine[]): void {
  try {
    localStorage.setItem(storageKey(), JSON.stringify(lines));
  } catch {
    // Storage may be full or blocked; the bag still works for this page.
  }
}

export function addLine(lines: BagLine[], productId: string, qty: number, personalization: Record<string, string>): BagLine[] {
  const answers = Object.fromEntries(
    Object.entries(personalization)
      .map(([id, value]) => [id, value.trim()] as const)
      .filter(([, value]) => value),
  );
  const key = lineKey(productId, answers);
  const existing = lines.find((line) => line.key === key);
  if (existing) {
    return lines.map((line) => (line.key === key ? { ...line, qty: Math.min(MAX_QTY, line.qty + qty) } : line));
  }
  if (lines.length >= MAX_LINES) return lines;
  return [...lines, { key, productId, qty: Math.min(MAX_QTY, Math.max(1, qty)), personalization: answers }];
}

export function setQty(lines: BagLine[], key: string, qty: number): BagLine[] {
  return lines.map((line) => (line.key === key ? { ...line, qty: Math.min(MAX_QTY, Math.max(1, qty)) } : line));
}

export function removeLine(lines: BagLine[], key: string): BagLine[] {
  return lines.filter((line) => line.key !== key);
}

/** The cart as the API takes it: product, quantity and answers, nothing else. */
export function cartItems(lines: BagLine[]) {
  return lines.map((line) => ({ product_id: line.productId, qty: line.qty, personalization: line.personalization }));
}
