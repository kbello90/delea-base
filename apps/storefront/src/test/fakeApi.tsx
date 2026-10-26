import type { Schemas } from "@delea/api-client";
import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { createQueryClient } from "../lib/api";
import { ApiContext, createStorefrontApi } from "../lib/apiClient";
import { ShopRoutes } from "./ShopRoutes";

type Store = Schemas["PublicStoreOut"];
type Product = Schemas["PublicProductOut"];
type Page = Schemas["PublicPageOut"];

export const STORE: Store = {
  business_name: "DELEA",
  tagline: "Jewelry Operations",
  primary_color: "#6B2737",
  accent_color: "#B8935B",
  logo_url: null,
  hero_eyebrow: "Made to order",
  hero_title: "Handcrafted pieces in 14K gold and 925 silver",
  hero_subtitle: "Each piece is made for you.",
  collection_title: "The collection",
  font_preset: "classic",
  corner_style: "square",
  mark_style: "monogram",
  hero_button: null,
  currency: "USD",
  contact_email: "hello@example.com",
  pages: [
    { slug: "custom-orders", title: "How custom orders work" },
    { slug: "returns", title: "Returns" },
  ],
  has_made_to_order: true,
  // Checkout options arrive with Slice A2's API; the A1 screens don't use them yet.
  checkout_enabled: false,
  shipping: null,
  pickup: null,
  payment_choices: [],
  tax_rate_bps: 800,
  tax_shipping: false,
};

export function makeProduct(overrides: Partial<Product> = {}): Product {
  return {
    id: "a1000000-0000-0000-0000-000000000001",
    name: "Custom Initial Necklace",
    description: "A fine chain with your initial.",
    metal: "gold_14k",
    price_cents: 9800,
    sale_price_cents: null,
    sale_ends_at: null,
    unit_price_cents: 9800,
    availability: "in_stock",
    lead_time_note: null,
    images: [],
    personalization: [],
    ...overrides,
  };
}

export const PRODUCTS: Product[] = [
  makeProduct(),
  makeProduct({
    id: "a1000000-0000-0000-0000-000000000002",
    name: "Birth Flower Pendant",
    metal: "rose_gold",
    price_cents: 11000,
    sale_price_cents: 9900,
    unit_price_cents: 9900,
    availability: "low",
  }),
  makeProduct({
    id: "a1000000-0000-0000-0000-000000000003",
    name: "Engagement Ring",
    metal: "silver_925",
    price_cents: 14500,
    unit_price_cents: 14500,
    availability: "out_of_stock",
  }),
  makeProduct({
    id: "a1000000-0000-0000-0000-000000000004",
    name: "Monogram Ring",
    metal: "other",
    price_cents: 12550,
    unit_price_cents: 12550,
    availability: "made_to_order",
    lead_time_note: "Ships in 2-3 weeks",
    images: [
      { url: "http://127.0.0.1:55321/storage/v1/object/public/product-images/a-1200.webp", thumb_url: "http://127.0.0.1:55321/storage/v1/object/public/product-images/a-400.webp", width: 1200, height: 1200, is_primary: true },
      { url: "http://127.0.0.1:55321/storage/v1/object/public/product-images/b-1200.webp", thumb_url: "http://127.0.0.1:55321/storage/v1/object/public/product-images/b-400.webp", width: 1200, height: 1200, is_primary: false },
    ],
  }),
];

export const PAGES: Record<string, Page> = {
  "custom-orders": { slug: "custom-orders", title: "How custom orders work", body: "Choose a piece.\n\nWe make it after you order." },
  returns: { slug: "returns", title: "Returns", body: "Unworn pieces within 30 days." },
};

export const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export type Responder = (url: URL) => Response;

/** A fake public API: the store, products and pages above, overridable per path. */
export function fakeApi({ store = STORE, products = PRODUCTS, pages = PAGES, routes: custom = {} }: { store?: Store; products?: Product[]; pages?: Record<string, Page>; routes?: Record<string, Responder> } = {}) {
  return vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    const path = url.pathname;
    if (custom[path]) return custom[path](url);
    if (path === "/api/v1/public/store") return json(200, store);
    if (path === "/api/v1/public/products") return json(200, products);
    const product = path.match(/^\/api\/v1\/public\/products\/([^/]+)$/);
    if (product) {
      const found = products.find((p) => p.id === product[1]);
      return found ? json(200, found) : json(404, { detail: "Product not found" });
    }
    const page = path.match(/^\/api\/v1\/public\/pages\/([^/]+)$/);
    if (page) return pages[page[1]] ? json(200, pages[page[1]]) : json(404, { detail: "Page not found" });
    return json(404, { detail: "Not found" });
  });
}

// MemoryRouter + useRoutes renders the same route objects as the app's data router, without
// the data router's fetch-based navigation (jsdom's AbortSignal doesn't satisfy Node's fetch).
export function renderShop(path = "/", fetch = fakeApi()) {
  const api = createStorefrontApi(fetch);
  const utils = render(
    <ApiContext.Provider value={api}>
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <ShopRoutes />
        </MemoryRouter>
      </QueryClientProvider>
    </ApiContext.Provider>,
  );
  return { ...utils, fetch };
}
