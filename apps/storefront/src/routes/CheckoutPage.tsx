import { unwrap } from "@delea/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { Answers } from "../components/PersonalizationFields";
import { TurnstileWidget } from "../components/Turnstile";
import { refusal, useQuote, type Fulfillment, type Order, type PaymentChoice, type Quote, type Store } from "../lib/api";
import { useApi } from "../lib/apiClient";
import { cartItems } from "../lib/bag";
import { useBag } from "../lib/bagContext";
import { nextAttempt, orderFingerprint, type Attempt } from "../lib/checkoutKey";
import { formatMoney } from "../lib/format";
import { UNAVAILABLE_ORDERING, orderErrorMessage, problemMessage } from "../lib/messages";
import { US_STATES } from "../lib/usStates";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useShop } from "../lib/useShop";

interface Form {
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  line1: string;
  line2: string;
  city: string;
  state: string;
  postalCode: string;
  note: string;
}

const EMPTY: Form = { firstName: "", lastName: "", email: "", phone: "", line1: "", line2: "", city: "", state: "", postalCode: "", note: "" };
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const ZIP = /^[0-9]{5}(-[0-9]{4})?$/;

function formErrors(form: Form, fulfillment: Fulfillment | null, payment: PaymentChoice | null): Partial<Record<keyof Form | "fulfillment" | "payment", string>> {
  const errors: Partial<Record<keyof Form | "fulfillment" | "payment", string>> = {};
  if (!form.firstName.trim()) errors.firstName = "Enter your first name.";
  if (!form.lastName.trim()) errors.lastName = "Enter your last name.";
  if (!EMAIL.test(form.email.trim())) errors.email = "Enter an email address like you@example.com.";
  if (!fulfillment) errors.fulfillment = "Choose shipping or pickup.";
  if (!payment) errors.payment = "Choose how you'll pay.";
  if (fulfillment === "ship") {
    if (!form.line1.trim()) errors.line1 = "Enter the street address.";
    if (!form.city.trim()) errors.city = "Enter the city.";
    if (!form.state) errors.state = "Choose the state.";
    if (!ZIP.test(form.postalCode.trim())) errors.postalCode = "Enter a 5-digit ZIP code.";
  }
  return errors;
}

const PAYMENT_LABELS: Record<PaymentChoice, { title: string; text: string }> = {
  bank_transfer: { title: "Bank transfer", text: "We send the details; your order is held until the payment arrives." },
  pay_on_pickup: { title: "Pay when you pick up", text: "Cash or card at the studio." },
};

export function CheckoutPage() {
  const store = useShop();
  const bag = useBag();
  // The fill time the API checks runs from here, when the checkout page loaded.
  const loadedAt = useRef(Date.now());
  useDocumentTitle(`Checkout · ${store.business_name}`);
  if (bag.lines.length === 0) return <Navigate to="/bag" replace />;
  if (!store.checkout_enabled) {
    return (
      <section className="section notice-page">
        <h1>Checkout</h1>
        <p className="notice" role="status">
          {UNAVAILABLE_ORDERING}
        </p>
        <Link to="/bag" className="text-link">
          Back to your bag
        </Link>
      </section>
    );
  }
  return <CheckoutForm store={store} loadedAt={loadedAt.current} />;
}

function CheckoutForm({ store, loadedAt }: { store: Store; loadedAt: number }) {
  const api = useApi();
  const bag = useBag();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const items = useMemo(() => cartItems(bag.lines), [bag.lines]);

  const fulfillmentOptions: Fulfillment[] = [...(store.shipping ? (["ship"] as const) : []), ...(store.pickup ? (["pickup"] as const) : [])];
  const [fulfillment, setFulfillment] = useState<Fulfillment | null>(fulfillmentOptions[0] ?? null);
  const paymentOptions = store.payment_choices.filter((c) => c !== "pay_on_pickup" || fulfillment === "pickup") as PaymentChoice[];
  const [payment, setPayment] = useState<PaymentChoice | null>(paymentOptions[0] ?? null);
  const [form, setForm] = useState<Form>(EMPTY);
  const [errors, setErrors] = useState<ReturnType<typeof formErrors>>({});
  const [website, setWebsite] = useState(""); // the hidden field: people never fill it
  const attempt = useRef<Attempt | null>(null);
  // Set before the request and cleared when it settles: a second click in the same moment
  // (before the button re-renders as disabled) sends nothing.
  const submitting = useRef(false);
  const [token, setToken] = useState<string | null>(null);
  const [resetCount, setResetCount] = useState(0);
  const [newTotal, setNewTotal] = useState<Quote | null>(null); // a changed price to confirm
  const [message, setMessage] = useState<string | null>(null);

  const quote = useQuote(items, fulfillment);
  // Paying on pickup is only for pickup orders.
  useEffect(() => {
    if (payment && !paymentOptions.includes(payment)) setPayment(paymentOptions[0] ?? null);
  }, [payment, paymentOptions]);

  const set = (key: keyof Form, value: string) => setForm((f) => ({ ...f, [key]: value }));
  const shown = newTotal ?? quote.data;

  const place = useMutation({
    mutationFn: (body: Record<string, unknown>) => unwrap(api.POST("/api/v1/public/orders", { body: body as never })) as Promise<Order>,
    // The confirmation empties the bag: emptying it here would send this page to the bag first.
    onSuccess: (order) => navigate(`/orders/${order.public_token}`, { replace: true, state: { placed: order } }),
    onSettled: () => {
      submitting.current = false;
    },
    onError: (error) => {
      const { reason, quote: fresh } = refusal(error);
      if (fresh && (reason === "price_changed" || reason === "unavailable")) {
        queryClient.setQueryData(["quote", items, fulfillment], fresh);
        setNewTotal(reason === "price_changed" ? fresh : null);
      }
      setMessage(orderErrorMessage(error));
      setResetCount((n) => n + 1); // a Turnstile token is single use
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting.current || !shown) return;
    const found = formErrors(form, fulfillment, payment);
    setErrors(found);
    setMessage(null);
    if (Object.keys(found).length > 0) {
      document.getElementById(`checkout-${Object.keys(found)[0]}`)?.focus();
      return;
    }
    if (shown.has_problems) {
      setMessage("Some pieces in your bag need attention. Go back to your bag to fix them.");
      return;
    }
    const body: Record<string, unknown> = {
      items,
      fulfillment,
      payment_choice: payment,
      // The total the shopper is agreeing to; the server prices the order itself and refuses
      // it if this differs. Never a price to charge.
      expected_total_cents: shown.total_cents,
      contact: {
        first_name: form.firstName.trim(),
        last_name: form.lastName.trim(),
        email: form.email.trim(),
        ...(form.phone.trim() ? { phone: form.phone.trim() } : {}),
      },
      ...(fulfillment === "ship"
        ? {
            ship_to: {
              line1: form.line1.trim(),
              ...(form.line2.trim() ? { line2: form.line2.trim() } : {}),
              city: form.city.trim(),
              state: form.state,
              postal_code: form.postalCode.trim(),
            },
          }
        : {}),
      ...(form.note.trim() ? { note: form.note.trim() } : {}),
    };
    attempt.current = nextAttempt(attempt.current, orderFingerprint(body));
    submitting.current = true;
    place.mutate({
      ...body,
      checkout_key: attempt.current.key,
      website,
      elapsed_ms: Date.now() - loadedAt,
      ...(token ? { turnstile_token: token } : {}),
    });
  }

  const money = (cents: number) => formatMoney(cents, store.currency);
  const field = (key: keyof Form, label: string, props: Record<string, unknown> = {}) => (
    <div className="form-field">
      <label htmlFor={`checkout-${key}`}>{label}</label>
      <input
        id={`checkout-${key}`}
        value={form[key]}
        onChange={(e) => set(key, e.target.value)}
        aria-invalid={Boolean(errors[key])}
        aria-describedby={errors[key] ? `checkout-${key}-error` : undefined}
        disabled={place.isPending}
        {...props}
      />
      {errors[key] && (
        <span id={`checkout-${key}-error`} className="field-error">
          {errors[key]}
        </span>
      )}
    </div>
  );

  return (
    <section className="section checkout" aria-labelledby="checkout-title">
      <h1 id="checkout-title">Checkout</h1>
      <div className="checkout__layout">
        <OrderSummary quote={shown} loading={quote.isPending} money={money} fulfillment={fulfillment} />

        <form className="checkout__form" onSubmit={submit} noValidate aria-label="Your details">
          {(message || quote.isError) && (
            <div className="notice notice--error" role="alert">
              <p>{message ?? "The prices couldn't be loaded. Check your connection and try again."}</p>
              {shown?.has_problems && (
                <Link to="/bag" className="text-link">
                  Review your bag
                </Link>
              )}
            </div>
          )}
          {newTotal && (
            <p className="notice notice--warn" role="alert">
              The new total is <strong>{money(newTotal.total_cents)}</strong>. Check your order, then confirm to place it.
            </p>
          )}

          <fieldset>
            <legend>Contact</legend>
            <div className="form-row">
              {field("firstName", "First name", { autoComplete: "given-name", required: true })}
              {field("lastName", "Last name", { autoComplete: "family-name", required: true })}
            </div>
            {field("email", "Email", { type: "email", autoComplete: "email", inputMode: "email", required: true })}
            {field("phone", "Phone (for delivery updates, optional)", { type: "tel", autoComplete: "tel" })}
          </fieldset>

          <fieldset>
            <legend>Delivery</legend>
            <div className="choices" role="radiogroup" aria-label="Delivery" aria-describedby={errors.fulfillment ? "checkout-fulfillment-error" : undefined}>
              {fulfillmentOptions.map((option) => (
                <label key={option} className={`choice ${fulfillment === option ? "choice--on" : ""}`}>
                  <input type="radio" name="fulfillment" value={option} checked={fulfillment === option} onChange={() => setFulfillment(option)} disabled={place.isPending} />
                  <span className="choice__text">
                    <strong>{option === "ship" ? "Ship to me" : "Pick up"}</strong>
                    <span>
                      {option === "ship" && store.shipping
                        ? store.shipping.free_over_cents != null
                          ? `${money(store.shipping.flat_cents)}, free over ${money(store.shipping.free_over_cents)}`
                          : money(store.shipping.flat_cents)
                        : store.pickup?.note ?? "Free"}
                    </span>
                  </span>
                </label>
              ))}
            </div>
            {errors.fulfillment && (
              <span id="checkout-fulfillment-error" className="field-error">
                {errors.fulfillment}
              </span>
            )}
            {fulfillment === "ship" && (
              <>
                {field("line1", "Street address", { autoComplete: "address-line1" })}
                {field("line2", "Apartment, suite (optional)", { autoComplete: "address-line2" })}
                <div className="form-row">
                  {field("city", "City", { autoComplete: "address-level2" })}
                  <div className="form-field">
                    <label htmlFor="checkout-state">State</label>
                    <select id="checkout-state" value={form.state} onChange={(e) => set("state", e.target.value)} autoComplete="address-level1" aria-invalid={Boolean(errors.state)} aria-describedby={errors.state ? "checkout-state-error" : undefined} disabled={place.isPending}>
                      <option value="">Choose…</option>
                      {US_STATES.map(([code, name]) => (
                        <option key={code} value={code}>
                          {name}
                        </option>
                      ))}
                    </select>
                    {errors.state && (
                      <span id="checkout-state-error" className="field-error">
                        {errors.state}
                      </span>
                    )}
                  </div>
                  {field("postalCode", "ZIP code", { autoComplete: "postal-code", inputMode: "numeric" })}
                </div>
                <p className="field-hint">We ship within the US.</p>
              </>
            )}
          </fieldset>

          <fieldset>
            <legend>Payment</legend>
            <div className="choices" role="radiogroup" aria-label="Payment">
              {paymentOptions.map((option) => (
                <label key={option} className={`choice ${payment === option ? "choice--on" : ""}`}>
                  <input type="radio" name="payment" value={option} checked={payment === option} onChange={() => setPayment(option)} disabled={place.isPending} />
                  <span className="choice__text">
                    <strong>{PAYMENT_LABELS[option].title}</strong>
                    <span>{PAYMENT_LABELS[option].text}</span>
                  </span>
                </label>
              ))}
            </div>
            {errors.payment && <span className="field-error">{errors.payment}</span>}
          </fieldset>

          <div className="form-field">
            <label htmlFor="checkout-note">A note for the shop (optional)</label>
            <textarea id="checkout-note" rows={3} maxLength={500} value={form.note} onChange={(e) => set("note", e.target.value)} disabled={place.isPending} />
          </div>

          {/* A field people never see or reach; bots that fill every field give themselves away. */}
          <div className="hp" aria-hidden="true">
            <label htmlFor="checkout-website">Website</label>
            <input id="checkout-website" name="website" type="text" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
          </div>

          <TurnstileWidget onToken={setToken} resetCount={resetCount} onError={() => setMessage("The security check couldn't load. Refresh the page and try again.")} />

          <div className="pay-bar">
            <button type="submit" className="button button--primary button--block pay-bar__button" disabled={place.isPending || !shown || quote.isFetching} aria-busy={place.isPending}>
              {place.isPending ? "Placing your order…" : shown ? `${newTotal ? "Confirm new total and place order" : "Place order"} · ${money(shown.total_cents)}` : "Place order"}
            </button>
            <p className="pay-bar__note">You pay later, by the method you chose. Nothing is charged online.</p>
          </div>
        </form>
      </div>
    </section>
  );
}

function OrderSummary({ quote, loading, money, fulfillment }: { quote: Quote | undefined; loading: boolean; money: (cents: number) => string; fulfillment: Fulfillment | null }) {
  return (
    <aside className="summary" aria-label="Order summary">
      {loading || !quote ? (
        <p className="summary__loading" role="status">
          Working out your total…
        </p>
      ) : (
        <>
          <ul className="summary__lines">
            {quote.lines.map((line, i) => (
              <li key={`${line.product_id}-${i}`}>
                <div>
                  <span>
                    {line.name ?? "Not available"}
                    {line.qty > 1 && <span className="summary__qty"> × {line.qty}</span>}
                  </span>
                  <Answers answers={line.personalization} />
                  {line.problem && <span className="field-error">{problemMessage(line.problem)}</span>}
                </div>
                <span>{line.line_total_cents != null ? money(line.line_total_cents) : "—"}</span>
              </li>
            ))}
          </ul>
          <dl className="totals">
            <div>
              <dt>{fulfillment === "pickup" ? "Pickup" : "Shipping"}</dt>
              <dd>{quote.shipping_cents == null ? "—" : quote.shipping_cents === 0 ? "Free" : money(quote.shipping_cents)}</dd>
            </div>
            <div>
              <dt>Tax ({quote.tax_rate_bps / 100}%)</dt>
              <dd>{money(quote.tax_cents)}</dd>
            </div>
            <div className="totals__total">
              <dt>Total</dt>
              <dd>{money(quote.total_cents)}</dd>
            </div>
          </dl>
        </>
      )}
    </aside>
  );
}
