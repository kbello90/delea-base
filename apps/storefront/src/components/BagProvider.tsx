import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { addLine, loadBag, removeLine, saveBag, setQty, type BagLine } from "../lib/bag";
import { BagContext, type Bag } from "../lib/bagContext";

export function BagProvider({ children }: { children: ReactNode }) {
  const [lines, setLines] = useState<BagLine[]>(loadBag);

  useEffect(() => saveBag(lines), [lines]);

  // Another tab of the same shop changed the bag.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key?.startsWith("delea-bag:")) setLines(loadBag());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const add = useCallback((productId: string, qty: number, answers: Record<string, string>) => setLines((current) => addLine(current, productId, qty, answers)), []);
  const changeQty = useCallback((key: string, qty: number) => setLines((current) => setQty(current, key, qty)), []);
  const remove = useCallback((key: string) => setLines((current) => removeLine(current, key)), []);
  const clear = useCallback(() => setLines([]), []);

  const bag = useMemo<Bag>(
    () => ({ lines, count: lines.reduce((sum, line) => sum + line.qty, 0), add, setQty: changeQty, remove, clear }),
    [lines, add, changeQty, remove, clear],
  );
  return <BagContext.Provider value={bag}>{children}</BagContext.Provider>;
}
