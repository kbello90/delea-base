import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { useInvoiceCounts } from "../invoices/api";
import { ROLE_LABEL, routesFor } from "../routes/routeTable";
import { useBrand } from "../theme/useBrand";
import { UserMenu } from "./UserMenu";

/** Website orders waiting for payment, on the Invoices menu item (owner and staff). */
function AwaitingBadge() {
  const counts = useInvoiceCounts(true);
  const n = counts.data?.awaiting_payment_web ?? 0;
  if (n === 0) return null;
  return (
    <span className="nav-badge" aria-label={`${n} website ${n === 1 ? "order" : "orders"} awaiting payment`}>
      {n}
    </span>
  );
}

/** Sidebar + topbar layout (from docs/delea-demo.html). Rendered only for signed-in members. */
export function AppShell() {
  const { state } = useAuth();
  const role = state.status === "signedIn" && state.claims ? state.claims.appRole : null;
  if (!role) throw new Error("AppShell rendered without a membership");

  const brand = useBrand(role);
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();

  // On narrow screens the sidebar is a drawer: close it after navigating or on Escape.
  useEffect(() => setNavOpen(false), [location.pathname]);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setNavOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className={navOpen ? "overlay overlay--open" : "overlay"} onClick={() => setNavOpen(false)} aria-hidden="true" />

      <aside id="sidebar" className={navOpen ? "sidebar sidebar--open" : "sidebar"} aria-label="Sidebar">
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">
            {brand.logoUrl ? <img src={brand.logoUrl} alt="" /> : brand.businessName.charAt(0)}
          </span>
          <span className="brand-name" data-testid="brand-name">
            {brand.businessName}
          </span>
        </div>
        <nav className="menu" aria-label="Main">
          <ul>
            {routesFor(role).map((route) => (
              <li key={route.path}>
                <NavLink to={route.path} className={({ isActive }) => (isActive ? "menu-item active" : "menu-item")}>
                  <span className="glyph" aria-hidden="true">
                    {route.glyph}
                  </span>
                  {route.label}
                  {route.path === "/invoices" && (role === "owner" || role === "staff") && <AwaitingBadge />}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="sidebar-foot">Signed in as {ROLE_LABEL[role]}</div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <button
            type="button"
            className="mobile-toggle"
            aria-controls="sidebar"
            aria-expanded={navOpen}
            onClick={() => setNavOpen((open) => !open)}
          >
            <span aria-hidden="true">☰</span>
            <span className="visually-hidden">Menu</span>
          </button>
          <span className="topbar-title">{brand.businessName}</span>
          <UserMenu email={state.status === "signedIn" ? state.email : null} roleLabel={ROLE_LABEL[role]} />
        </header>
        <main id="main" className="content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
