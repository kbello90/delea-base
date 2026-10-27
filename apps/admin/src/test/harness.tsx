import { createApiClient, type AppRole, type Schemas } from "@delea/api-client";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AppProviders } from "../App";
import type { AuthBackend, SessionInfo } from "../auth/authBackend";
import { TestRoutes } from "./TestRoutes";

export const API = "http://api.test";

function base64url(value: object): string {
  return btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** An unsigned token with the given claims (the UI only reads claims; it never verifies). */
export function makeToken(claims: Record<string, unknown>): string {
  return `${base64url({ alg: "none" })}.${base64url({ sub: "user-1", ...claims })}.sig`;
}

export const TENANT_A = "11111111-1111-1111-1111-111111111111";

export const TENANT_B = "22222222-2222-2222-2222-222222222222";

/** A member's token; each role (and tenant) is a different user unless `sub` says otherwise. */
export function tokenFor(role: AppRole, tenant = TENANT_A, sub = `user-${role}-${tenant.slice(0, 1)}`): string {
  return makeToken({ sub, tenant_id: tenant, app_role: role });
}

/** The tenant_id in a request's bearer token (for fakes that answer per tenant). */
export function tenantOf(request: Request): string | null {
  const token = request.headers.get("Authorization")?.replace(/^Bearer /, "");
  const part = token?.split(".")[1];
  if (!part) return null;
  const payload = JSON.parse(atob(part.replace(/-/g, "+").replace(/_/g, "/"))) as { tenant_id?: string };
  return payload.tenant_id ?? null;
}

/** A fake AuthBackend holding one session. signIn succeeds unless `control.signInResult` is set. */
export function fakeBackend(token: string | null, email = "person@example.com") {
  let session: SessionInfo | null = token ? { accessToken: token, email } : null;
  const listeners = new Set<(s: SessionInfo | null) => void>();
  const emit = () => listeners.forEach((listener) => listener(session));
  const control = {
    signInResult: null as string | null,
    signInToken: tokenFor("owner"),
    /** verifyLink: a message to fail with, else it signs in with linkToken. */
    verifyResult: null as string | null,
    linkToken: tokenFor("staff"),
    setPasswordResult: null as string | null,
  };

  return {
    control,
    getSession: vi.fn(async () => session),
    onChange: vi.fn((listener: (s: SessionInfo | null) => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    }),
    signIn: vi.fn(async (signInEmail: string, _password: string): Promise<string | null> => {
      if (control.signInResult) return control.signInResult;
      session = { accessToken: control.signInToken, email: signInEmail };
      emit();
      return null;
    }),
    verifyLink: vi.fn(async (_kind: "invite" | "recovery", _tokenHash: string): Promise<string | null> => {
      if (control.verifyResult) return control.verifyResult;
      session = { accessToken: control.linkToken, email };
      emit();
      return null;
    }),
    setPassword: vi.fn(async (_password: string): Promise<string | null> => control.setPasswordResult),
    signOut: vi.fn(async () => {
      session = null;
      emit();
    }),
    getAccessToken: vi.fn(async () => session?.accessToken ?? null),
    refresh: vi.fn(async () => session?.accessToken ?? null),
  } satisfies AuthBackend & { control: object };
}

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export const DEFAULT_BRAND_RESPONSE: Schemas["BrandConfigOut"] = {
  tenant_id: TENANT_A,
  business_name: "Atelier Rose",
  tagline: null,
  primary_color: "#112233",
  accent_color: "#445566",
  logo_url: null,
  hero_title: null,
  hero_subtitle: null,
  hero_eyebrow: null,
  collection_title: null,
  font_preset: "classic",
  corner_style: "square",
  mark_style: "monogram",
  hero_cta_label: null,
  hero_cta_url: null,
  updated_at: "2026-10-20T00:00:00Z",
};

/** Card payments (Slice B2): Stripe set up on the server, no account connected yet. */
export const PAYMENTS_RESPONSE: Schemas["PaymentSettingsOut"] = {
  stripe_configured: true,
  is_demo: false,
  account: null,
  card_payments: false,
};

export const STOREFRONT_RESPONSE: Schemas["StorefrontSettingsOut"] = {
  storefront_enabled: false,
  contact_email: null,
  return_policy: null,
  privacy_policy: null,
  terms: null,
  custom_orders_info: null,
  ship_enabled: false,
  ship_flat_cents: 0,
  free_ship_over_cents: null,
  tax_shipping: false,
  pickup_enabled: false,
  pickup_note: null,
  bank_transfer_enabled: false,
  pay_on_pickup_enabled: false,
  manual_payment_instructions: null,
  updated_at: "2026-10-20T00:00:00Z",
};

/** Tenant A's seed data this month (see supabase/seed.sql), as GET /dashboard/summary returns it. */
export const SUMMARY_RESPONSE: Schemas["DashboardSummary"] = {
  currency_unit: "cents",
  period_basis: "UTC",
  month_start: "2026-10-01T00:00:00Z",
  year_start: "2026-01-01T00:00:00Z",
  mtd: {
    revenue_cents: 9800,
    tax_reserve_cents: 784,
    expenses_cents: 91300,
    deductible_expenses_cents: 84500,
    net_profit_cents: -81500,
  },
  ytd: {
    revenue_cents: 1234500,
    tax_reserve_cents: 98760,
    expenses_cents: 456700,
    deductible_expenses_cents: 400000,
    net_profit_cents: 777800,
  },
  deposits_held_cents: 0,
  deposits_held_count: 0,
  low_stock_products: 1,
  low_stock_materials: 2,
  invoice_counts: { quote: 1, paid: 1, in_production: 0, shipped: 3, delivered: 4, refunded: 5, cancelled: 6 },
};

export const MONTHLY_RESPONSE: Schemas["MonthlyTrend"] = {
  currency_unit: "cents",
  period_basis: "UTC",
  months: [
    { month_start: "2026-05-01", revenue_cents: 0, expenses_cents: 0 },
    { month_start: "2026-06-01", revenue_cents: 250000, expenses_cents: 120000 },
    { month_start: "2026-07-01", revenue_cents: 310000, expenses_cents: 99900 },
    { month_start: "2026-08-01", revenue_cents: 0, expenses_cents: 45000 },
    { month_start: "2026-09-01", revenue_cents: 664700, expenses_cents: 100500 },
    { month_start: "2026-10-01", revenue_cents: 9800, expenses_cents: 91300 },
  ],
};

export const COMPANY_RESPONSE: Schemas["CompanySettingsOut"] = {
  tenant_id: TENANT_A,
  legal_name: "DELEA Jewelry LLC",
  ein: "12-3456789",
  address: null,
  currency: "USD",
  default_language: "en",
  enabled_languages: ["en"],
  default_tax_rate_bps: 800,
  next_quote_number: 41,
  next_invoice_number: 89,
  updated_at: "2026-10-20T00:00:00Z",
};

type Responder = (request: Request) => Response | Promise<Response>;

const DEFAULT_RESPONSES: Record<string, unknown> = {
  "/api/v1/settings/brand": DEFAULT_BRAND_RESPONSE,
  "/api/v1/settings/company": COMPANY_RESPONSE,
  "/api/v1/settings/storefront": STOREFRONT_RESPONSE,
  "/api/v1/settings/payments": PAYMENTS_RESPONSE,
  "/api/v1/settings/currency": { currency: "USD" },
  "/api/v1/dashboard/summary": SUMMARY_RESPONSE,
  "/api/v1/dashboard/monthly": MONTHLY_RESPONSE,
  "/api/v1/invoices": [],
  "/api/v1/expenses": [],
  "/api/v1/expenses/summary": { count: 0, total_cents: 0, deductible_cents: 0 },
};

/** Fake backend API: realistic responses for the built pages by default; override per path with `routes`. */
export function fakeApi(routes: Record<string, Responder> = {}) {
  return vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    const respond = routes[path];
    if (respond) return respond(request);
    if (path in DEFAULT_RESPONSES) return jsonResponse(200, DEFAULT_RESPONSES[path]);
    return jsonResponse(404, { detail: "Not found" });
  });
}

// MemoryRouter + useRoutes renders the same route objects as the app's data router, without
// the data router's fetch Request (whose jsdom AbortSignal Node's Request rejects).
export function renderApp({
  path = "/",
  backend = fakeBackend(tokenFor("owner")),
  fetch = fakeApi(),
}: {
  path?: string;
  backend?: AuthBackend;
  fetch?: (request: Request) => Promise<Response>;
} = {}) {
  const api = createApiClient({ baseUrl: API, auth: backend, fetch });
  const location = { pathname: path, search: "" };
  const utils = render(
    <AppProviders backend={backend} api={api}>
      <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TestRoutes location={location} />
      </MemoryRouter>
    </AppProviders>,
  );
  return { ...utils, location, backend, fetch };
}
