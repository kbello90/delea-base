import { Link } from "react-router-dom";
import { HeroArt } from "../components/Art";
import { ProductCard } from "../components/ProductCard";
import { isRateLimited, useProducts, type Store } from "../lib/api";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useShop } from "../lib/useShop";
import { isSafeHeroLink } from "../lib/theme";

function Hero({ store }: { store: Store }) {
  const customOrders = store.pages.some((p) => p.slug === "custom-orders");
  const second = store.hero_button && isSafeHeroLink(store.hero_button.url) ? store.hero_button : null;
  const external = second?.url.startsWith("https://");
  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="hero__copy">
        {store.hero_eyebrow && <p className="hero__eyebrow">{store.hero_eyebrow}</p>}
        <h1 id="hero-title">{store.hero_title || store.business_name}</h1>
        {store.hero_subtitle && <p className="hero__sub">{store.hero_subtitle}</p>}
        <div className="hero__actions">
          <a href="#collection" className="button button--gold">
            Shop the collection
          </a>
          {customOrders && (
            <Link to="/pages/custom-orders" className="button button--ghost">
              How custom orders work
            </Link>
          )}
          {second && (
            <a href={second.url} className="button button--ghost" {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>
              {second.label}
            </a>
          )}
        </div>
      </div>
      <div className="hero__panel">
        <HeroArt />
      </div>
    </section>
  );
}

function TrustStrip({ store }: { store: Store }) {
  // Only what is true for this shop today (email updates and card payments come later).
  const items = store.has_made_to_order ? [{ title: "Made for you", text: "Made-to-order pieces are crafted after you buy them." }] : [];
  if (items.length === 0) return null;
  return (
    <ul className="trust" aria-label="Why shop with us">
      {items.map((item) => (
        <li key={item.title}>
          <strong>{item.title}</strong>
          <span>{item.text}</span>
        </li>
      ))}
    </ul>
  );
}

function Collection({ store, madeToOrderOnly }: { store: Store; madeToOrderOnly: boolean }) {
  const products = useProducts();
  const heading = madeToOrderOnly ? "Made to order" : store.collection_title || "The collection";

  let body;
  if (products.isPending) {
    body = (
      <ul className="grid" aria-busy="true" aria-label="Loading pieces">
        {Array.from({ length: 4 }, (_, i) => (
          <li key={i} className="card card--skeleton" />
        ))}
      </ul>
    );
  } else if (products.isError) {
    body = (
      <div className="notice" role="alert">
        <p>{isRateLimited(products.error) ? "Too many requests from your connection. Try again in a minute." : "The pieces couldn't be loaded."}</p>
        <button type="button" className="button button--outline" onClick={() => void products.refetch()}>
          Try again
        </button>
      </div>
    );
  } else {
    const shown = madeToOrderOnly ? products.data.filter((p) => p.availability === "made_to_order") : products.data;
    body =
      shown.length === 0 ? (
        <p className="notice">New pieces are coming soon.</p>
      ) : (
        <>
          <p className="section__meta">
            {shown.length} {shown.length === 1 ? "piece" : "pieces"} · prices in {store.currency}
          </p>
          <ul className="grid">
            {shown.map((product) => (
              <ProductCard key={product.id} product={product} currency={store.currency} />
            ))}
          </ul>
        </>
      );
  }

  return (
    <section id="collection" className="section" aria-labelledby="collection-title">
      <h2 id="collection-title">{heading}</h2>
      {body}
    </section>
  );
}

export function Home({ madeToOrderOnly = false }: { madeToOrderOnly?: boolean }) {
  const store = useShop();
  useDocumentTitle(madeToOrderOnly ? `Made to order · ${store.business_name}` : store.business_name);
  return (
    <>
      {!madeToOrderOnly && <Hero store={store} />}
      <Collection store={store} madeToOrderOnly={madeToOrderOnly} />
      {!madeToOrderOnly && <TrustStrip store={store} />}
    </>
  );
}
