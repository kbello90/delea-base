import { useState } from "react";
import { percentToBps } from "../customers/customerForm";
import type { Product } from "../products/api";
import type { LineItemIn } from "./api";

export type PersonalizationField = Product["personalization"][number];

export interface CartLine {
  /** Stable per line: a personalized product gets one line per piece, each with its answers. */
  key: string;
  product: Pick<Product, "id" | "name" | "sku"> & Partial<Pick<Product, "personalization" | "made_to_order">>;
  qty: number;
  /** field id -> answer, as typed */
  answers: Record<string, string>;
}

export const MAX_QTY = 999;

let lastKey = 0;
export const lineKey = () => `line-${++lastKey}`;

const fieldsOf = (line: Pick<CartLine, "product">) => line.product.personalization ?? [];

/** Why an answer can't be used, as the server checks it (public.personalization_snapshot). */
export function answerError(field: PersonalizationField, raw: string | undefined): string | null {
  const answer = (raw ?? "").trim();
  if (!answer) return field.required ? "Required" : null;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(answer)) return "Remove the special characters";
  if (field.kind === "choice" && !(field.choices ?? []).includes(answer)) return "Pick one of the choices";
  if (field.kind === "text" && field.max_length !== null && field.max_length !== undefined && answer.length > field.max_length) {
    return `At most ${field.max_length} characters`;
  }
  return null;
}

const lineAnswered = (line: CartLine) => fieldsOf(line).every((f) => answerError(f, line.answers[f.id]) === null);

/**
 * Add a product (one more on its line, or a new line for each personalized piece), change a
 * quantity or an answer, or remove a line.
 */
export function useCart(initial: CartLine[] = []) {
  const [lines, setLines] = useState(initial);
  const answered = (l: CartLine) =>
    Object.fromEntries(Object.entries(l.answers).filter(([, v]) => v.trim() !== "")) as Record<string, string>;
  return {
    lines,
    add: (product: CartLine["product"]) =>
      setLines((current) => {
        if ((product.personalization ?? []).length > 0) return [...current, { key: lineKey(), product, qty: 1, answers: {} }];
        return current.some((l) => l.product.id === product.id)
          ? current.map((l) => (l.product.id === product.id ? { ...l, qty: Math.min(MAX_QTY, l.qty + 1) } : l))
          : [...current, { key: lineKey(), product, qty: 1, answers: {} }];
      }),
    setQty: (key: string, qty: number) => setLines((current) => current.map((l) => (l.key === key ? { ...l, qty } : l))),
    setAnswer: (key: string, fieldId: string, value: string) =>
      setLines((current) => current.map((l) => (l.key === key ? { ...l, answers: { ...l.answers, [fieldId]: value } } : l))),
    remove: (key: string) => setLines((current) => current.filter((l) => l.key !== key)),
    reset: () => setLines([]),
    /** What is created or saved: each line with its answers (blank ones left out). */
    items: lines.map((l): LineItemIn => ({ product_id: l.product.id, qty: l.qty, personalization: answered(l) })),
    /** What is priced: answers don't change prices, so typing them doesn't re-price the cart. */
    priceItems: lines.map((l): LineItemIn => ({ product_id: l.product.id, qty: l.qty, personalization: {} })),
    valid: lines.length > 0 && lines.every((l) => Number.isInteger(l.qty) && l.qty >= 1 && l.qty <= MAX_QTY),
    /** Every personalization question answered as the product asks. */
    answered: lines.every(lineAnswered),
    madeToOrder: lines.some((l) => l.product.made_to_order === true),
  };
}

export type Cart = ReturnType<typeof useCart>;

/** "8.25" -> 825 basis points (at most 2 decimals); empty means no override. */
export function taxOverride(raw: string): { bps: number | null; error?: string } {
  if (!raw.trim()) return { bps: null };
  const { bps, error } = percentToBps(raw);
  if (error) return { bps: null, error: error.replace("The discount", "The tax rate") };
  return { bps: bps ?? null };
}
