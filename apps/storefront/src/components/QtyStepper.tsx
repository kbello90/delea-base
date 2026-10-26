import { MAX_QTY } from "../lib/bag";

/** − n + with 44 px buttons; the number can be typed too. */
export function QtyStepper({ value, onChange, label, disabled = false }: { value: number; onChange: (qty: number) => void; label: string; disabled?: boolean }) {
  const set = (n: number) => onChange(Math.min(MAX_QTY, Math.max(1, Number.isFinite(n) ? Math.trunc(n) : 1)));
  return (
    <div className="qty" role="group" aria-label={label}>
      <button type="button" className="qty__button" onClick={() => set(value - 1)} disabled={disabled || value <= 1} aria-label="One fewer">
        −
      </button>
      <input
        className="qty__input"
        type="number"
        inputMode="numeric"
        min={1}
        max={MAX_QTY}
        value={value}
        onChange={(e) => set(Number(e.target.value))}
        aria-label="Quantity"
        disabled={disabled}
      />
      <button type="button" className="qty__button" onClick={() => set(value + 1)} disabled={disabled || value >= MAX_QTY} aria-label="One more">
        +
      </button>
    </div>
  );
}
