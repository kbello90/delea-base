import { Link, useNavigate } from "react-router-dom";
import { Answers } from "../components/PersonalizationFields";
import { QtyStepper } from "../components/QtyStepper";
import { useQuote, type QuoteLine, type Store } from "../lib/api";
import { cartItems } from "../lib/bag";
import { useBag } from "../lib/bagContext";
import { formatMoney } from "../lib/format";
import { problemMessage } from "../lib/messages";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useShop } from "../lib/useShop";

export function BagPage() {
  const store = useShop();
  const bag = useBag();
  const navigate = useNavigate();
  const quote = useQuote(cartItems(bag.lines), null);
  useDocumentTitle(`Your bag · ${store.business_name}`);

  if (bag.lines.length === 0) {
    return (
      <section className="section notice-page">
        <h1>Your bag is empty</h1>
        <Link to="/" className="button button--primary">
          Shop the collection
        </Link>
      </section>
    );
  }

  const money = (cents: number) => formatMoney(cents, store.currency);
  const lines = quote.data?.lines ?? [];
  const blocked = !quote.data || quote.data.has_problems || !store.checkout_enabled;

  return (
    <section className="section bag" aria-labelledby="bag-title">
      <h1 id="bag-title">Your bag</h1>
      {quote.isError && (
        <div className="notice notice--error" role="alert">
          <p>The prices couldn't be loaded. Check your connection and try again.</p>
          <button type="button" className="button button--outline" onClick={() => void quote.refetch()}>
            Try again
          </button>
        </div>
      )}
      <ul className="bag__lines" aria-busy={quote.isFetching}>
        {bag.lines.map((line, index) => (
          <BagLineRow key={line.key} store={store} productId={line.productId} quoted={lines[index]} qty={line.qty} onQty={(qty) => bag.setQty(line.key, qty)} onRemove={() => bag.remove(line.key)} money={money} />
        ))}
      </ul>
      {quote.data && (
        <dl className="totals">
          <div>
            <dt>Subtotal</dt>
            <dd>{money(quote.data.subtotal_cents)}</dd>
          </div>
          <div>
            <dt>Shipping</dt>
            <dd>Chosen at checkout</dd>
          </div>
          <div>
            <dt>Tax ({quote.data.tax_rate_bps / 100}%)</dt>
            <dd>{money(quote.data.tax_cents)}</dd>
          </div>
          <div className="totals__total">
            <dt>Total before shipping</dt>
            <dd>{money(quote.data.total_cents)}</dd>
          </div>
        </dl>
      )}
      {!store.checkout_enabled && (
        <p className="notice" role="status">
          Ordering is temporarily unavailable. Your bag is kept; please come back soon.
        </p>
      )}
      {quote.data?.has_problems && (
        <p className="notice notice--error" role="alert">
          Some pieces need your attention before you can check out.
        </p>
      )}
      <div className="bag__actions">
        <Link to="/" className="text-link">
          Keep shopping
        </Link>
        <button type="button" className="button button--primary" disabled={blocked} onClick={() => navigate("/checkout")}>
          Check out
        </button>
      </div>
    </section>
  );
}

function BagLineRow({
  store,
  productId,
  quoted,
  qty,
  onQty,
  onRemove,
  money,
}: {
  store: Store;
  productId: string;
  quoted: QuoteLine | undefined;
  qty: number;
  onQty: (qty: number) => void;
  onRemove: () => void;
  money: (cents: number) => string;
}) {
  const name = quoted?.name ?? (quoted ? "A piece that's no longer available" : "Loading…");
  return (
    <li className="bag__line">
      <div className="bag__line-main">
        <p className="bag__name">{quoted?.name ? <Link to={`/products/${productId}`}>{name}</Link> : name}</p>
        <Answers answers={quoted?.personalization} />
        {quoted?.lead_time_note && <p className="field-hint">Made for you. {quoted.lead_time_note}</p>}
        {quoted?.problem && (
          <p className="field-error" role="alert">
            {problemMessage(quoted.problem)}{" "}
            {quoted.problem === "options" && <Link to={`/products/${productId}`}>Choose again</Link>}
          </p>
        )}
      </div>
      <div className="bag__line-side">
        <QtyStepper value={qty} onChange={onQty} label={`Quantity of ${quoted?.name ?? "this piece"}`} disabled={!quoted?.name} />
        <p className="bag__price">{quoted?.line_total_cents != null ? money(quoted.line_total_cents) : "—"}</p>
        <button type="button" className="text-button" onClick={onRemove} aria-label={`Remove ${quoted?.name ?? "this piece"} from your bag`}>
          Remove
        </button>
      </div>
      {store.currency && quoted?.unit_price_cents != null && qty > 1 && <p className="field-hint bag__each">{money(quoted.unit_price_cents)} each</p>}
    </li>
  );
}
