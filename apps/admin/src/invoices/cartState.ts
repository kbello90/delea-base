import { useState } from "react";
import { percentToBps } from "../customers/customerForm";
import type { Product } from "../products/api";
import type { LineItemIn } from "./api";

export interface CartLine {
  product: Pick<Product, "id" | "name" | "sku">;
  qty: number;
}

export const MAX_QTY = 999;

/** Add one of a product (merging with its line), change a quantity, or remove a line. */
export function useCart(initial: CartLine[] = []) {
  const [lines, setLines] = useState(initial);
  return {
    lines,
    add: (product: CartLine["product"]) =>
      setLines((current) =>
        current.some((l) => l.product.id === product.id)
          ? current.map((l) => (l.product.id === product.id ? { ...l, qty: Math.min(MAX_QTY, l.qty + 1) } : l))
          : [...current, { product, qty: 1 }],
      ),
    setQty: (productId: string, qty: number) =>
      setLines((current) => current.map((l) => (l.product.id === productId ? { ...l, qty } : l))),
    remove: (productId: string) => setLines((current) => current.filter((l) => l.product.id !== productId)),
    reset: () => setLines([]),
    items: lines.map((l): LineItemIn => ({ product_id: l.product.id, qty: l.qty })),
    valid: lines.length > 0 && lines.every((l) => Number.isInteger(l.qty) && l.qty >= 1 && l.qty <= MAX_QTY),
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
