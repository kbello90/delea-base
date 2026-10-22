import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { formatMoney } from "../lib/money";
import { useCurrency } from "../lib/useCurrency";
import {
  documentNumber,
  methodLabel,
  useChangeStatus,
  useCreateInvoice,
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

/** After payment: what was sold, and whether it is marked delivered yet. */
function Receipt({
  sale,
  deliverError,
  delivering,
  onRetryDeliver,
  onNewSale,
}: {
  sale: Invoice;
  deliverError: string | null;
  delivering: boolean;
  onRetryDeliver: () => void;
  onNewSale: () => void;
}) {
  const currency = useCurrency();
  const money = (cents: number) => (currency.status === "success" ? formatMoney(cents, currency.currency) : "…");
  return (
    <section className="receipt" aria-labelledby="receipt-heading">
      <h2 id="receipt-heading">Paid · {documentNumber(sale)}</h2>
      <p>
        {sale.customer_id ? sale.customer_name : "Walk-in sale"}
        {sale.payment_method && ` · ${methodLabel(sale.payment_method)}`}
      </p>
      <ul className="receipt__lines">
        {sale.items?.map((line) => (
          <li key={line.id}>
            <span>
              {line.qty} × {line.description}
            </span>
            <span>{money(line.line_total_cents)}</span>
          </li>
        ))}
      </ul>
      <Totals totals={sale} pending={false} label="Total paid" />
      {sale.order_status === "delivered" ? (
        <p className="receipt__status" role="status">
          Handed over: marked delivered.
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
        <Button type="button" onClick={onNewSale}>
          New sale
        </Button>
        <Link to={`/invoices/${sale.id}`} className="btn-text">
          Open {documentNumber(sale)}
        </Link>
      </div>
    </section>
  );
}

export function SellPage() {
  const cart = useCart();
  const [customer, setCustomer] = useState<PickedCustomer | null>(null);
  const [method, setMethod] = useState<PaymentMethod | null>(null);
  const [sale, setSale] = useState<Invoice | null>(null);
  const [deliverError, setDeliverError] = useState<string | null>(null);
  const request: PreviewRequest | null = cart.valid ? { customer_id: customer?.id ?? null, items: cart.items } : null;
  const preview = usePreview(sale ? null : request);
  const create = useCreateInvoice();
  const deliver = useChangeStatus();
  const currency = useCurrency();

  const priced = Boolean(preview.data) && !preview.isFetching;
  const shortage = Boolean(preview.data?.stock_shortages.length);
  const busy = create.isPending || deliver.isPending;
  const ready = Boolean(request) && priced && !shortage && method !== null && !busy;

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
    create.mutate(
      { customer_id: request.customer_id, items: request.items, pay_now: true, payment_method: method },
      {
        onSuccess: (paid) => {
          setSale(paid);
          markDelivered(paid);
        },
      },
    );
  }

  function newSale() {
    cart.reset();
    setCustomer(null);
    setMethod(null);
    setSale(null);
    setDeliverError(null);
    create.reset();
    deliver.reset();
  }

  return (
    <div className="sell">
      <PageHeader title="New sale" description="An in-store sale: paid now and handed over." />
      {sale ? (
        <Receipt
          sale={sale}
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
            <PaymentMethodChoice name="sale-method" value={method} onChange={setMethod} disabled={busy} />
          </section>
          {create.isError && (
            <p className="form-error" role="alert">
              The sale wasn't recorded: {message(create.error)}
            </p>
          )}
          <Button type="button" className="take-payment" onClick={takePayment} disabled={!ready} aria-busy={create.isPending}>
            {create.isPending
              ? "Taking payment…"
              : priced && preview.data && !shortage && currency.status === "success"
                ? `Take payment · ${formatMoney(preview.data.total_cents, currency.currency)}`
                : "Take payment"}
          </Button>
        </>
      )}
    </div>
  );
}
