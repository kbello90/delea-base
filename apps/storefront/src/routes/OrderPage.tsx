import { ApiError } from "@delea/api-client";
import { useEffect, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { Answers } from "../components/PersonalizationFields";
import { isNotFound, isRateLimited, isStripeCheckout, leave, useOrder, usePayNow, type Order } from "../lib/api";
import { useBag } from "../lib/bagContext";
import { formatMoney } from "../lib/format";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useShop } from "../lib/useShop";

/** Stripe takes no card payment under 50 cents: a smaller balance is settled with the shop. */
export const MIN_CARD_CENTS = 50;

const STATUS: Record<Order["status"], { label: string; text: string }> = {
  awaiting_payment: { label: "Waiting for payment", text: "We'll start as soon as your payment arrives." },
  deposit_paid: { label: "Deposit received", text: "Thank you, your deposit arrived." },
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

/** How long to keep asking after Stripe sends the shopper back: its webhook usually lands in seconds. */
const CONFIRM_POLL_MS = 3_000;
const CONFIRM_POLLS = 20;

/** Stripe's hosted Checkout for the balance. */
function PayNow({ token, label }: { token: string; label: string }) {
  const pay = usePayNow(token);
  const error = pay.error instanceof ApiError ? pay.error.message : pay.error ? "Check your connection and try again." : null;
  return (
    <div className="order__paynow">
      <button
        type="button"
        className="button"
        disabled={pay.isPending}
        onClick={() =>
          pay.mutate(undefined, {
            onSuccess: (out) => {
              if (isStripeCheckout(out.checkout_url)) leave.to(out.checkout_url);
            },
          })
        }
      >
        {pay.isPending ? "Opening the secure payment page…" : label}
      </button>
      <p className="field-hint">You pay on Stripe&apos;s secure page; your card details never reach the shop.</p>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function OrderPage() {
  const { token } = useParams();
  const store = useShop();
  const location = useLocation();
  const placed = (location.state as { placed?: Order } | null)?.placed;
  // Back from Stripe (?paid=1): the webhook records the payment within seconds; keep asking.
  const [params] = useSearchParams();
  const returned = params.get("paid") === "1";
  const [polls, setPolls] = useState(0);
  const order = useOrder(
    token,
    placed?.public_token === token ? placed : undefined,
    returned && polls < CONFIRM_POLLS ? CONFIRM_POLL_MS : false,
  );
  const fetchedAt = order.dataUpdatedAt;
  useEffect(() => {
    if (returned && fetchedAt) setPolls((n) => n + 1);
  }, [returned, fetchedAt]);
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
  // Part paid: a deposit is in, the rest is still due.
  const partPaid = o.amount_paid_cents > 0 && o.balance_cents > 0;
  const open = o.balance_cents > 0 && o.status !== "cancelled" && o.status !== "refunded";
  // Back from Stripe and not recorded yet: say so instead of asking to pay again.
  const confirming = returned && open && polls < CONFIRM_POLLS;
  const card = store.payment_choices.includes("card") && open && o.balance_cents >= MIN_CARD_CENTS && !confirming;
  const payLabel = `Pay ${money(o.balance_cents)} by card`;
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
        <span>
          {status.text}
          {partPaid && <> Balance due {money(o.balance_cents)}.</>}
        </span>
      </div>

      {confirming && (
        <p className="order__confirming" role="status">
          Thank you. Your card payment is being confirmed; this page updates by itself.
        </p>
      )}

      {o.payment_choice === "card" && o.status === "awaiting_payment" && !confirming && (
        <section className="order__pay" aria-labelledby="pay-title">
          <h2 id="pay-title">Pay by card</h2>
          {card ? (
            <>
              <p>Your order is placed. It is confirmed once the payment goes through.</p>
              <PayNow token={o.public_token} label={payLabel} />
            </>
          ) : (
            <p>Card payments aren&apos;t available right now. Please contact the shop to pay.</p>
          )}
        </section>
      )}

      {o.payment_instructions && o.status === "awaiting_payment" && o.payment_choice !== "card" && (
        <section className="order__pay" aria-labelledby="pay-title">
          <h2 id="pay-title">{o.payment_choice === "bank_transfer" ? "How to pay" : "Paying when you pick up"}</h2>
          <p className="order__instructions">{o.payment_instructions}</p>
          <p className="field-hint">Use your order number {o.order_ref} as the reference.</p>
          {card && <PayNow token={o.public_token} label={`Or pay ${money(o.balance_cents)} by card now`} />}
        </section>
      )}

      {partPaid && (
        <section className="order__pay" aria-labelledby="balance-title">
          <h2 id="balance-title">Balance due</h2>
          <p>
            Paid so far {money(o.amount_paid_cents)} of {money(o.total_cents)}.
          </p>
          {o.balance_cents < MIN_CARD_CENTS ? (
            <p className="order__instructions">Pay the rest with the shop: {money(o.balance_cents)}.</p>
          ) : (
            <>
              {o.payment_instructions && o.payment_choice !== "card" && (
                <>
                  <p className="order__instructions">{o.payment_instructions}</p>
                  <p className="field-hint">Use your order number {o.order_ref} as the reference.</p>
                </>
              )}
              {card && <PayNow token={o.public_token} label={`Pay the rest, ${money(o.balance_cents)}, by card`} />}
            </>
          )}
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
          {partPaid && (
            <>
              <div>
                <dt>Paid so far</dt>
                <dd>{money(o.amount_paid_cents)}</dd>
              </div>
              <div className="totals__total">
                <dt>Balance due</dt>
                <dd>{money(o.balance_cents)}</dd>
              </div>
            </>
          )}
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
