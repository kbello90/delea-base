import { unwrap } from "@delea/api-client";
import { useQuery } from "@tanstack/react-query";
import { useState, type KeyboardEvent } from "react";
import { useApi } from "../lib/api";
import { formatMoney } from "../lib/money";
import { useCurrency } from "../lib/useCurrency";
import { bpsToPercent } from "../customers/customerForm";
import type { Product } from "../products/api";
import type { InvoicePreview } from "./api";
import { answerError, MAX_QTY, type Cart, type CartLine } from "./cartState";

/**
 * Search the catalogue (drafts included, tagged) and add products to the cart. A barcode
 * scanner types the code and presses Enter: Enter adds one of the product whose SKU is exactly
 * the text (any case), then clears the box for the next scan. Nothing else is ever added.
 */
export function ProductPicker({ onAdd, large = false }: { onAdd: (product: Product) => void; large?: boolean }) {
  const api = useApi();
  const [q, setQ] = useState("");
  const [scan, setScan] = useState<{ text: string; error: boolean } | null>(null);
  const text = q.trim();
  const results = useQuery({
    queryKey: ["products", "picker", text],
    queryFn: () => unwrap(api.GET("/api/v1/products", { params: { query: { q: text || undefined, limit: 12 } } })),
  });
  const currency = useCurrencyCode();

  async function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const code = q.trim();
    if (!code) return;
    setQ("");
    try {
      const found = await unwrap(api.GET("/api/v1/products", { params: { query: { sku: code, limit: 2 } } }));
      if (found.length === 1) {
        onAdd(found[0]);
        setScan({ text: `Added ${found[0].name} (${found[0].sku}).`, error: false });
      } else if (found.length === 0) {
        setScan({ text: `No product with SKU ${code}. Nothing was added.`, error: true });
      } else {
        setQ(code); // SKUs that differ only in case: the cashier picks from the list
        setScan({ text: `More than one product has SKU ${code}. Pick it from the list.`, error: true });
      }
    } catch {
      setScan({ text: `SKU ${code} couldn't be looked up. Nothing was added; scan it again.`, error: true });
    }
  }

  return (
    <div className="product-picker">
      <div className="filter">
        <label htmlFor="product-search">Find a product</label>
        <input
          id="product-search"
          type="search"
          placeholder="Name or SKU, or scan a barcode"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setScan(null);
          }}
          onKeyDown={(e) => void onKeyDown(e)}
          aria-describedby="product-search-scan"
        />
      </div>
      <p id="product-search-scan" className={scan?.error ? "form-error scan-status" : "muted scan-status"} role="status">
        {scan?.text ?? ""}
      </p>
      {results.isError && <p className="form-error">Products couldn't be loaded.</p>}
      <ul className={large ? "product-results product-results--large" : "product-results"} aria-label="Products">
        {results.data?.map((p) => (
          <li key={p.id}>
            <button type="button" className="product-option" onClick={() => onAdd(p)} aria-label={`Add ${p.name}`}>
              <span className="product-option__name">
                {p.name}
                {!p.is_published && <span className="tag tag--warn">Draft</span>}
              </span>
              <span className="product-option__meta muted">
                {p.sku} · {currency ? formatMoney(p.price_cents, currency) : "…"} ·{" "}
                {p.stock_qty === 0 ? "Out of stock" : `${p.stock_qty} in stock`}
              </span>
            </button>
          </li>
        ))}
        {results.data?.length === 0 && <li className="muted">No products match.</li>}
      </ul>
    </div>
  );
}

function useCurrencyCode(): string | null {
  const currency = useCurrency();
  return currency.status === "success" ? currency.currency : null;
}

/**
 * The cart's lines with the server's prices. Prices and totals appear only once the server
 * has priced exactly these lines; while it is pricing they read "Pricing…".
 */
export function CartTable({
  cart,
  preview,
  pricing,
  busy = false,
}: {
  cart: Cart;
  preview: InvoicePreview | undefined;
  pricing: boolean;
  busy?: boolean;
}) {
  const currency = useCurrencyCode();
  const money = (cents: number) => (currency ? formatMoney(cents, currency) : "…");
  if (cart.lines.length === 0) return <p className="muted cart-empty">No items yet. Add a product above.</p>;
  return (
    <div className="table-wrap">
      <table className="data-grid cart">
        <caption className="visually-hidden">Items</caption>
        <thead>
          <tr>
            <th scope="col">Item</th>
            <th scope="col">Qty</th>
            <th scope="col">Unit price</th>
            <th scope="col">Line total</th>
            <th scope="col">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {cart.lines.map((line, index) => {
            const priced = preview?.lines[index];
            const qtyId = `qty-${line.key}`;
            return (
              <tr key={line.key}>
                <td>
                  <span className="cell-title">{line.product.name}</span>
                  <span className="muted customer-contact">{line.product.sku}</span>
                  <LineAnswers cart={cart} line={line} busy={busy} />
                </td>
                <td data-label="Qty">
                  <div className="qty">
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label={`One fewer ${line.product.name}`}
                      disabled={busy || line.qty <= 1}
                      onClick={() => cart.setQty(line.key, line.qty - 1)}
                    >
                      −
                    </button>
                    <label htmlFor={qtyId} className="visually-hidden">
                      Quantity of {line.product.name}
                    </label>
                    <input
                      id={qtyId}
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={MAX_QTY}
                      value={Number.isNaN(line.qty) ? "" : line.qty}
                      disabled={busy}
                      onChange={(e) => cart.setQty(line.key, Number.parseInt(e.target.value, 10))}
                    />
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label={`One more ${line.product.name}`}
                      disabled={busy || line.qty >= MAX_QTY}
                      onClick={() => cart.setQty(line.key, line.qty + 1)}
                    >
                      +
                    </button>
                  </div>
                </td>
                <td data-label="Unit price">{priced && !pricing ? money(priced.unit_price_cents) : "Pricing…"}</td>
                <td data-label="Line total">{priced && !pricing ? money(priced.line_total_cents) : "Pricing…"}</td>
                <td className="row-actions">
                  <button
                    type="button"
                    className="btn-text btn-text--danger"
                    disabled={busy}
                    onClick={() => cart.remove(line.key)}
                    aria-label={`Remove ${line.product.name}`}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The product's personalization questions for this line, answered here (Slice C1). */
function LineAnswers({ cart, line, busy }: { cart: Cart; line: CartLine; busy: boolean }) {
  const fields = line.product.personalization ?? [];
  if (fields.length === 0) return null;
  return (
    <fieldset className="line-fields" disabled={busy}>
      <legend className="visually-hidden">Personalization for {line.product.name}</legend>
      {fields.map((field) => {
        const id = `${line.key}-${field.id}`;
        const value = line.answers[field.id] ?? "";
        const error = answerError(field, value);
        // An unanswered required question reads "Required"; a wrong answer shows as typed.
        return (
          <div key={field.id} className="line-fields__field">
            <label htmlFor={id}>
              {field.label}
              {field.required ? "" : " (optional)"}
            </label>
            {field.kind === "choice" ? (
              <select
                id={id}
                value={value}
                onChange={(e) => cart.setAnswer(line.key, field.id, e.target.value)}
                aria-invalid={Boolean(error)}
                aria-describedby={error ? `${id}-error` : undefined}
              >
                <option value="">{field.required ? "Choose…" : "None"}</option>
                {(field.choices ?? []).map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={id}
                type="text"
                value={value}
                onChange={(e) => cart.setAnswer(line.key, field.id, e.target.value)}
                aria-invalid={Boolean(error)}
                aria-describedby={error ? `${id}-error` : undefined}
              />
            )}
            {error && (
              <span id={`${id}-error`} className="field-error">
                {error}
              </span>
            )}
          </div>
        );
      })}
    </fieldset>
  );
}

/** Subtotal, tax and total exactly as the server priced them, or nothing while it prices. */
export function Totals({
  totals,
  pending,
  label = "Total",
}: {
  totals: (Pick<InvoicePreview, "subtotal_cents" | "tax_rate_bps" | "tax_cents" | "total_cents"> & { shipping_cents?: number }) | undefined;
  pending: boolean;
  label?: string;
}) {
  const currency = useCurrencyCode();
  if (!totals || pending || !currency) {
    return (
      <p className="totals totals--pending" role="status">
        {pending ? "Pricing…" : "The total appears once the items are priced."}
      </p>
    );
  }
  return (
    <dl className="totals" aria-label="Totals">
      <div>
        <dt>Subtotal</dt>
        <dd>{formatMoney(totals.subtotal_cents, currency)}</dd>
      </div>
      {totals.shipping_cents ? (
        <div>
          <dt>Shipping</dt>
          <dd>{formatMoney(totals.shipping_cents, currency)}</dd>
        </div>
      ) : null}
      <div>
        <dt>Tax ({bpsToPercent(totals.tax_rate_bps)}%)</dt>
        <dd>{formatMoney(totals.tax_cents, currency)}</dd>
      </div>
      <div className="totals__total">
        <dt>{label}</dt>
        <dd>{formatMoney(totals.total_cents, currency)}</dd>
      </div>
    </dl>
  );
}

/** "Not enough stock for SKU …" for each product the cart needs more of. */
export function StockShortages({ preview }: { preview: InvoicePreview | undefined }) {
  if (!preview?.stock_shortages.length) return null;
  return (
    <ul className="shortages" role="alert">
      {preview.stock_shortages.map((s) => (
        <li key={s.product_id}>
          Not enough stock for SKU {s.sku}: {s.available} available, {s.needed} needed.
        </li>
      ))}
    </ul>
  );
}
