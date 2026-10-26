import { createContext, useContext } from "react";
import type { BagLine } from "./bag";

export interface Bag {
  lines: BagLine[];
  count: number; // pieces, not lines
  add(productId: string, qty: number, personalization: Record<string, string>): void;
  setQty(key: string, qty: number): void;
  remove(key: string): void;
  clear(): void;
}

export const BagContext = createContext<Bag | null>(null);

export function useBag(): Bag {
  const bag = useContext(BagContext);
  if (!bag) throw new Error("useBag must be used inside <BagProvider>");
  return bag;
}
