import { useEffect } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { Answers } from "../components/PersonalizationFields";
import { isNotFound, isRateLimited, useOrder, type Order } from "../lib/api";
import { useBag } from "../lib/bagContext";
import { formatMoney } from "../lib/format";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useShop } from "../lib/useShop";

const STATUS: Record<Order["status"], { label: string; text: string }> = {
  awaiting_payment: { label: "Waiting for payment", text: "We'll start as soon as your payment arrives." },
  paid: { label: "Paid", text: "Thank you, your payment arrived." },
  in_production: { label: "In production", text: "Your piece is being made." },
  shipped: { label: "On its way", text: "Your order has left the studio." },
  delivered: { label: "Delivered", text: "Enjoy your piece." },
  cancelled: { label: "Cancelled", text: "This order was cancelled." },
  refunded: { label: "Refunded", text: "This order was refunded." },
};

/**
 * The tracking link holds a secret (the order's public token): while this page is open the
 * browser is told never to send it on as a referrer (also set for /orders/* in vercel.json).
 */
function useNoReferrer() {
  useEffect(() => {
    const meta = document.querySelector<HTMLMetaElement>('meta[name="referrer"]');
    const previous = meta?.content;
    const element = meta ?? Object.assign(document.createElement("meta"), { name: "referrer" });
    element.content = "no-referrer";
    if (!meta) document.head.appendChild(element);
    return () => {
      if (meta && previous !== undefined) meta.content = previous;
      else element.remove();
    };
  }, []);
}

export function OrderPage() {
  const { token } = useParams();
  const store = useShop();
  const location = useLocation();
  const placed = (location.state as { placed?: Order } | null)?.placed;
  const order = useOrder(token, placed?.public_token === token ? placed : undefined);
  const bag = useBag();
  // Just placed: the bag's pieces are now this order.
  const clearBag = bag.clear;
  useEffect(() => {
    if (placed) clearBag();
  }, [placed, clearBag]);
  useNoReferrer();
  useDocumentTitle(order.data ? `Order ${order.data.order_ref} · ${store.business_name}` : store.business_name);

  if (order.isPending) {
    return (
      <div className="page-loading" role="status" aria-live="polite">
        Loading your order…
      </div>
    );
  }
  if (order.isError) {
    return (
      <section className="section notice-page">
        <h1>{isNotFound(order.error) ? "We couldn't find that order" : "Your order couldn't be loaded"}</h1>
        <p>
          {isNotFound(order.error)
            ? "Check that you opened the whole link from your confirmation."
            : isRateLimited(order.error)
              ? "Too many requests from your connection. Please wait a minute and try again."
              : "Check your connection and try again."}
        </p>
        {!isNotFound(order.error) && (
          <button type="button" className="button button--outline" onClick={() => void order.refetch()}>
            Try again
          </button>
        )}
      </section>
    );
  }

  const o = order.data;
  const money = (cents: number) => formatMoney(cents, store.currency);
  const status = STATUS[o.status];
  const justPlaced = Boolean(placed);
  return (
    <article className="section order" aria-labelledby="order-title">
      {justPlaced ? (
        <>
          <p className="order__eyebrow">Order placed</p>
          <h1 id="order-title">Thank you, {o.first_name}</h1>
          <p className="order__lead">
            Your order number is <strong className="order__ref">{o.order_ref}</strong>. Bookmark this page to follow your order: it's the only link to it.
          </p>
        </>
      ) : (
        <>
          <p className="order__eyebrow">Your order</p>
          <h1 id="order-title">
            Order <span className="order__ref">{o.order_ref}</span>
          </h1>
        </>
      )}

      <div className={`order__status order__status--${o.status}`} role="status">
        <strong>{status.label}</strong>
        <span>{status.text}</span>
      </div>

      {o.payment_instructions && o.status === "awaiting_payment" && (
        <section className="order__pay" aria-labelledby="pay-title">
          <h2 id="pay-title">{o.payment_choice === "bank_transfer" ? "How to pay" : "Paying when you pick up"}</h2>
          <p className="order__instructions">{o.payment_instructions}</p>
          <p className="field-hint">Use your order number {o.order_ref} as the reference.</p>
        </section>
      )}

      <section aria-labelledby="items-title">
        <h2 id="items-title">Your pieces</h2>
        <ul className="summary__lines">
          {o.lines.map((line, i) => (
            <li key={i}>
              <div>
                <span>
                  {line.description}
                  {line.qty > 1 && <span className="summary__qty"> × {line.qty}</span>}
                </span>
                <Answers answers={line.personalization} />
                {line.lead_time_note && <span className="field-hint">Made for you. {line.lead_time_note}</span>}
              </div>
              <span>{money(line.line_total_cents)}</span>
            </li>
          ))}
        </ul>
        <dl className="totals">
          <div>
            <dt>Subtotal</dt>
            <dd>{money(o.subtotal_cents)}</dd>
          </div>
          <div>
            <dt>{o.fulfillment === "pickup" ? "Pickup" : "Shipping"}</dt>
            <dd>{o.shipping_cents === 0 ? "Free" : money(o.shipping_cents)}</dd>
          </div>
          <div>
            <dt>Tax ({o.tax_rate_bps / 100}%)</dt>
            <dd>{money(o.tax_cents)}</dd>
          </div>
          <div className="totals__total">
            <dt>Total</dt>
            <dd>{money(o.total_cents)}</dd>
          </div>
        </dl>
      </section>

      <section className="order__delivery" aria-labelledby="delivery-title">
        <h2 id="delivery-title">{o.fulfillment === "pickup" ? "Pickup" : "Delivery"}</h2>
        {o.fulfillment === "pickup" ? <p>{o.pickup_note ?? "We'll tell you when it's ready."}</p> : <p>Shipping to {o.ship_city ?? "your address"}.</p>}
      </section>

      <Link to="/" className="text-link">
        Back to the shop
      </Link>
    </article>
  );
}
