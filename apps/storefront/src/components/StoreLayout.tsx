import { useEffect } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { isNotFound, isRateLimited, useStore, type Store } from "../lib/api";
import { applyStoreTheme, clearStoreTheme, isSafeImageUrl } from "../lib/theme";

function BrandMark({ store }: { store: Store }) {
  if (isSafeImageUrl(store.logo_url)) {
    return <img className="brand__logo" src={store.logo_url} alt={store.business_name} />;
  }
  return (
    <>
      {store.mark_style === "monogram" && (
        <span className="brand__monogram" aria-hidden="true">
          {store.business_name.trim().charAt(0).toUpperCase()}
        </span>
      )}
      <span className="brand__name">{store.business_name}</span>
    </>
  );
}

function Header({ store }: { store: Store }) {
  return (
    <header className="site-header">
      <div className="site-header__inner">
        <Link to="/" className="brand" aria-label={`${store.business_name}, home`}>
          <BrandMark store={store} />
        </Link>
        <nav className="site-nav" aria-label="Shop">
          <NavLink to="/" end>
            Shop all
          </NavLink>
          {store.has_made_to_order && <NavLink to="/made-to-order">Made to order</NavLink>}
        </nav>
      </div>
    </header>
  );
}

function Footer({ store }: { store: Store }) {
  return (
    <footer className="site-footer">
      <div className="site-footer__inner">
        <p>
          © {new Date().getFullYear()} {store.business_name}
        </p>
        <nav aria-label="Store information">
          {store.contact_email && <a href={`mailto:${store.contact_email}`}>Contact</a>}
          {store.pages.map((page) => (
            <Link key={page.slug} to={`/pages/${page.slug}`}>
              {page.title}
            </Link>
          ))}
        </nav>
      </div>
    </footer>
  );
}

/** Shown in place of the whole site when the store can't be shown. */
export function Unavailable({ title, message, onRetry }: { title: string; message: string; onRetry?: () => void }) {
  return (
    <main className="unavailable">
      <h1>{title}</h1>
      <p>{message}</p>
      {onRetry && (
        <button type="button" className="button button--primary" onClick={onRetry}>
          Try again
        </button>
      )}
    </main>
  );
}

export function StoreLayout() {
  const store = useStore();

  useEffect(() => {
    if (!store.data) return;
    applyStoreTheme(store.data);
    return () => clearStoreTheme();
  }, [store.data]);

  if (store.isPending) {
    return (
      <div className="page-loading" role="status" aria-live="polite">
        Loading the shop…
      </div>
    );
  }
  if (store.isError) {
    if (isNotFound(store.error)) {
      return <Unavailable title="This shop isn't available" message="Check the address, or come back later." />;
    }
    if (isRateLimited(store.error)) {
      return <Unavailable title="Please wait a moment" message="Too many requests came from your connection. Try again in a minute." />;
    }
    return <Unavailable title="The shop couldn't be loaded" message="Check your connection and try again." onRetry={() => void store.refetch()} />;
  }

  return (
    <div className="site">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Header store={store.data} />
      <main id="main" className="site-main">
        <Outlet context={store.data} />
      </main>
      <Footer store={store.data} />
    </div>
  );
}
