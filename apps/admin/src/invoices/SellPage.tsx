import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { formatMoney } from "../lib/money";
import { parseMoneyInput } from "../lib/moneyInput";
import { useCurrency } from "../lib/useCurrency";
import { PrintReceiptButton } from "../receipts/PrintReceipt";
import {
  documentNumber,
  invoiceMethodLabel,
  useChangeStatus,
  useCreateInvoice,
  useInvoiceList,
  usePreview,
  type Invoice,
  type PaymentMethod,
  type PreviewRequest,
} from "./api";
import { CartTable, ProductPicker, StockShortages, Totals } from "./Cart";
import { useCart } from "./cartState";
import { CustomerPicker, type PickedCustomer } from "./CustomerPicker";
import { PaymentMethodChoice } from "./InvoicePage";
import "./invoices.css";

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Please try again.");

type Mode = "full" | "deposit";

/** After the sale: what was taken, whether it is handed over, and the receipt to print. */
function SaleDone({
  sale,
  madeToOrder,
  deliverError,
  delivering,
  onRetryDeliver,
  onNewSale,
}: {
  sale: Invoice;
  madeToOrder: boolean;
  deliverError: string | null;
  delivering: boolean;
  onRetryDeliver: () => void;
  onNewSale: () => void;
}) {
  const currency = useCurrency();
  const money = (cents: number) => (currency.status === "success" ? formatMoney(cents, currency.currency) : "…");
  const deposit = sale.order_status === "quote";
  const method = invoiceMethodLabel(sale);
  return (
    <section className="receipt" aria-labelledby="receipt-heading">
      <h2 id="receipt-heading">
        {deposit ? `Deposit taken · ${documentNumber(sale)}` : `Paid · ${documentNumber(sale)}`}
      </h2>
      <p>
        {sale.customer_id ? sale.customer_name : "Walk-in sale"}
        {method && !deposit && ` · ${method}`}
      </p>
      <ul className="receipt__lines">
        {sale.items?.map((line) => (
          <li key={line.id}>
            <span>
              {line.qty} × {line.description}
              {line.personalization?.length ? (
                <span className="muted"> ({line.personalization.map((a) => `${a.label}: ${a.value}`).join(", ")})</span>
              ) : null}
            </span>
            <span>{money(line.line_total_cents)}</span>
          </li>
        ))}
      </ul>
      <Totals totals={sale} pending={false} label={deposit ? "Total" : "Total paid"} />
      {deposit ? (
        <p className="receipt__status" role="status">
          Deposit {money(sale.amount_paid_cents)} received. Balance due {money(sale.balance_cents)}: take it from {documentNumber(sale)} or
          from Find an order.
        </p>
      ) : sale.order_status === "delivered" ? (
        <p className="receipt__status" role="status">
          Handed over: marked delivered.
        </p>
      ) : madeToOrder ? (
        <p className="receipt__status" role="status">
          Made to order: {documentNumber(sale)} stays paid until it is made and handed over.
        </p>
      ) : (
        <div className="notice" role="alert">
          <p>
            The payment is recorded and {documentNumber(sale)} is paid, but it isn't marked delivered yet
            {deliverError ? `: ${deliverError}` : "."}
          </p>
          <Button type="button" variant="ghost" onClick={onRetryDeliver} disabled={delivering}>
            {delivering ? "Marking delivered…" : "Try marking delivered again"}
          </Button>
        </div>
      )}
      <div className="form-actions">
        <PrintReceiptButton invoice={sale} variant="primary" />
        <Button type="button" variant="ghost" onClick={onNewSale}>
          New sale
        </Button>
        <Link to={`/invoices/${sale.id}`} className="btn-text">
          Open {documentNumber(sale)}
        </Link>
      </div>
    </section>
  );
}

/** Find a quote holding a deposit (or not yet paid) to take the rest on its page. */
function FindOrder() {
  const [q, setQ] = useState("");
  const text = q.trim();
  const currency = useCurrency();
  const list = useInvoiceList(
    { status: "quote", customerId: null, dateFrom: null, dateTo: null, q: text, source: null, awaiting: false, page: 1 },
    text.length >= 2,
  );
  const open = (list.data?.items ?? []).filter((i) => !i.cancelled_at && i.balance_cents > 0);
  return (
    <details className="invoice-section find-order">
      <summary>Find an order to take the balance</summary>
      <div className="filter">
        <label htmlFor="find-order">Quote number or website order</label>
        <input id="find-order" type="search" placeholder="QTE-2026-012 or W-…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {text.length >= 2 && list.isSuccess && (
        <ul className="find-order__results" aria-label="Orders with a balance">
          {open.map((i) => (
            <li key={i.id}>
              <Link to={`/invoices/${i.id}`}>
                {documentNumber(i)} · {i.customer_name} · balance{" "}
                {currency.status === "success" ? formatMoney(i.balance_cents, currency.currency) : "…"}
              </Link>
            </li>
          ))}
          {open.length === 0 && <li className="muted">No open order matches.</li>}
        </ul>
      )}
    </details>
  );
}

export function SellPage() {
  const cart = useCart();
  const [customer, setCustomer] = useState<PickedCustomer | null>(null);
  const [mode, setMode] = useState<Mode>("full");
  const [depositRaw, setDepositRaw] = useState("");
  const [method, setMethod] = useState<PaymentMethod | null>(null);
  const [sale, setSale] = useState<Invoice | null>(null);
  const [madeToOrder, setMadeToOrder] = useState(false);
  const [deliverError, setDeliverError] = useState<string | null>(null);
  const request: PreviewRequest | null = cart.valid ? { customer_id: customer?.id ?? null, items: cart.priceItems } : null;
  const preview = usePreview(sale ? null : request);
  const create = useCreateInvoice();
  const deliver = useChangeStatus();
  const currency = useCurrency();

  const priced = Boolean(preview.data) && !preview.isFetching;
  const shortage = Boolean(preview.data?.stock_shortages.length);
  const busy = create.isPending || deliver.isPending;
  const total = priced ? (preview.data?.total_cents ?? 0) : 0;
  const deposit = parseMoneyInput(depositRaw);
  const depositError =
    mode !== "deposit" || !depositRaw.trim()
      ? null
      : !deposit.ok
        ? deposit.error
        : deposit.cents < 1
          ? "Enter an amount."
          : priced && deposit.cents >= total
            ? "A deposit is less than the total. To take it all, choose Pay in full."
            : null;
  const depositOk = mode === "full" || (deposit.ok && depositError === null && deposit.cents >= 1);
  // A deposit leaves the stock alone, so a shortage only stops paying in full.
  const ready = Boolean(request) && priced && cart.answered && (mode === "deposit" || !shortage) && depositOk && method !== null && !busy;
  const money = (cents: number) => (currency.status === "success" ? formatMoney(cents, currency.currency) : "…");

  function markDelivered(paid: Invoice) {
    setDeliverError(null);
    deliver.mutate(
      { id: paid.id, status: "delivered" },
      {
        onSuccess: (delivered) => setSale(delivered),
        // The sale stays paid; the receipt says so and offers a retry.
        onError: (e) => setDeliverError(message(e)),
      },
    );
  }

  function takePayment() {
    if (!request || !method) return;
    const toMake = cart.madeToOrder;
    const base = { customer_id: request.customer_id, items: cart.items, payment_method: method, source: "in_store" as const };
    create.mutate(mode === "deposit" && deposit.ok ? { ...base, pay_now: false, deposit_cents: deposit.cents } : { ...base, pay_now: true }, {
      onSuccess: (made) => {
        setSale(made);
        setMadeToOrder(toMake);
        // Handed over now, unless it is a deposit or a piece still to be made.
        if (made.order_status !== "quote" && !toMake) markDelivered(made);
      },
    });
  }

  function newSale() {
    cart.reset();
    setCustomer(null);
    setMode("full");
    setDepositRaw("");
    setMethod(null);
    setSale(null);
    setMadeToOrder(false);
    setDeliverError(null);
    create.reset();
    deliver.reset();
  }

  const label = create.isPending
    ? "Taking payment…"
    : mode === "deposit"
      ? deposit.ok && depositError === null && currency.status === "success"
        ? `Take deposit · ${money(deposit.cents)}`
        : "Take deposit"
      : priced && preview.data && !shortage && currency.status === "success"
        ? `Take payment · ${money(preview.data.total_cents)}`
        : "Take payment";

  return (
    <div className="sell">
      <PageHeader title="New sale" description="A sale at the counter: paid in full and handed over, or a deposit on a piece to be made." />
      {sale ? (
        <SaleDone
          sale={sale}
          madeToOrder={madeToOrder}
          deliverError={deliverError}
          delivering={deliver.isPending}
          onRetryDeliver={() => markDelivered(sale)}
          onNewSale={newSale}
        />
      ) : (
        <>
          <ProductPicker onAdd={cart.add} large />
          <section className="invoice-section" aria-labelledby="cart-heading">
            <h2 id="cart-heading">Cart</h2>
            <CartTable cart={cart} preview={preview.data} pricing={preview.isFetching} busy={busy} />
            {preview.isError && (
              <p className="form-error" role="alert">
                {message(preview.error)}
              </p>
            )}
            <StockShortages preview={preview.data} />
            <Totals totals={preview.data} pending={preview.isFetching} label="Total to pay" />
          </section>
          <section className="invoice-section">
            <CustomerPicker id="sale-customer" value={customer} onChange={setCustomer} allowWalkIn />
            <fieldset className="method-choice">
              <legend>Payment</legend>
              <div className="choice-row">
                <label className="check method-choice__option">
                  <input type="radio" name="sale-mode" checked={mode === "full"} onChange={() => setMode("full")} disabled={busy} />
                  Pay in full
                </label>
                <label className="check method-choice__option">
                  <input type="radio" name="sale-mode" checked={mode === "deposit"} onChange={() => setMode("deposit")} disabled={busy} />
                  Take a deposit
                </label>
              </div>
              {mode === "deposit" && (
                <div className="field deposit-field">
                  <label htmlFor="deposit-amount">Deposit amount</label>
                  <input
                    id="deposit-amount"
                    type="text"
                    inputMode="decimal"
                    value={depositRaw}
                    onChange={(e) => setDepositRaw(e.target.value)}
                    disabled={busy}
                    aria-invalid={Boolean(depositError)}
                    aria-describedby={depositError ? "deposit-amount-error" : "deposit-amount-hint"}
                  />
                  {depositError ? (
                    <span id="deposit-amount-error" className="field-error">
                      {depositError}
                    </span>
                  ) : (
                    <span id="deposit-amount-hint" className="field-hint">
                      Less than the total. The order stays a quote until the rest is paid; stock moves then.
                    </span>
                  )}
                </div>
              )}
            </fieldset>
            <PaymentMethodChoice name="sale-method" value={method} onChange={setMethod} disabled={busy} />
          </section>
          {!cart.answered && cart.lines.length > 0 && (
            <p className="field-hint" role="status">
              Answer the personalization questions in the cart first.
            </p>
          )}
          {create.isError && (
            <p className="form-error" role="alert">
              {mode === "deposit" ? "The deposit wasn't recorded" : "The sale wasn't recorded"}: {message(create.error)}
            </p>
          )}
          <Button type="button" className="take-payment" onClick={takePayment} disabled={!ready} aria-busy={create.isPending}>
            {label}
          </Button>
          <FindOrder />
        </>
      )}
    </div>
  );
}
