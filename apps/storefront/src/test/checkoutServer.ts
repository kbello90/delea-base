import type { Schemas } from "@delea/api-client";
import { fakeApi, json, makeProduct, PRODUCTS, STORE, type Responder } from "./fakeApi";

type Product = Schemas["PublicProductOut"];
type Quote = Schemas["QuoteOut"];
type Order = Schemas["OrderOut"];

export const SIZE_FIELD = "f0000000-0000-4000-8000-000000000001";
export const ENGRAVING_FIELD = "f0000000-0000-4000-8000-000000000002";
export const TOKEN = "70000000-0000-4000-8000-000000000001";

/** A shop that takes orders: ships for $6 (free over $150), pickup, both manual payments. */
export const OPEN_STORE: Schemas["PublicStoreOut"] = {
  ...STORE,
  checkout_enabled: true,
  shipping: { flat_cents: 600, free_over_cents: 15000 },
  pickup: { note: "Studio, Tuesday to Saturday" },
  payment_choices: ["bank_transfer", "pay_on_pickup"],
  tax_rate_bps: 800,
  tax_shipping: false,
};

export const RING: Product = makeProduct({
  id: "a1000000-0000-0000-0000-000000000009",
  name: "Halo Ring",
  metal: "gold_14k",
  price_cents: 14500,
  unit_price_cents: 14500,
  personalization: [
    { id: SIZE_FIELD, label: "Ring size", kind: "choice", required: true, max_length: null, choices: ["6", "7", "8"] },
    { id: ENGRAVING_FIELD, label: "Engraving", kind: "text", required: false, max_length: 10, choices: null },
  ],
});

export const CATALOG: Product[] = [...PRODUCTS, RING];

interface CartItem {
  product_id: string;
  qty: number;
  personalization?: Record<string, string>;
}

/** The test double of storefront_price_cart: retail price, $6 shipping (free over $150), 8% tax. */
export function priceCart(items: CartItem[], fulfillment: string | null, products: Product[] = CATALOG): Quote {
  let problems = false;
  const lines = items.map((item) => {
    const product = products.find((p) => p.id === item.product_id);
    const answers = item.personalization ?? {};
    const fields = product?.personalization ?? [];
    let problem: "unavailable" | "options" | null = product ? null : "unavailable";
    if (product && fields.some((f) => f.required && !answers[f.id]?.trim())) problem = "options";
    problems ||= problem !== null;
    const personalization = fields.filter((f) => answers[f.id]?.trim()).map((f) => ({ label: f.label, value: answers[f.id].trim() }));
    return {
      product_id: item.product_id,
      name: product?.name ?? null,
      qty: item.qty,
      unit_price_cents: product?.unit_price_cents ?? null,
      line_total_cents: product ? product.unit_price_cents * item.qty : null,
      personalization: personalization.length ? personalization : null,
      made_to_order: product?.availability === "made_to_order",
      lead_time_note: product?.availability === "made_to_order" ? product.lead_time_note : null,
      problem,
    };
  });
  const subtotal = lines.reduce((sum, l) => sum + (l.line_total_cents ?? 0), 0);
  const shipping = fulfillment === "ship" ? (subtotal >= 15000 ? 0 : 600) : fulfillment === "pickup" ? 0 : null;
  const tax = Math.floor((subtotal * 800 + 5000) / 10000);
  return { lines, subtotal_cents: subtotal, shipping_cents: shipping, tax_rate_bps: 800, tax_cents: tax, total_cents: subtotal + (shipping ?? 0) + tax, has_problems: problems };
}

export function orderFrom(body: Record<string, unknown>, quote: Quote): Order {
  const contact = body.contact as { first_name: string };
  const shipTo = body.ship_to as { city: string; state: string } | undefined;
  return {
    order_ref: "W-7K3P9QAB",
    public_token: TOKEN,
    placed_at: "2026-10-27T15:00:00Z",
    status: "awaiting_payment",
    fulfillment: body.fulfillment as "ship" | "pickup",
    payment_choice: body.payment_choice as "bank_transfer" | "pay_on_pickup",
    lines: quote.lines.map((l) => ({
      description: l.name ?? "",
      qty: l.qty,
      unit_price_cents: l.unit_price_cents ?? 0,
      line_total_cents: l.line_total_cents ?? 0,
      personalization: l.personalization,
      lead_time_note: l.lead_time_note,
    })),
    subtotal_cents: quote.subtotal_cents,
    shipping_cents: quote.shipping_cents ?? 0,
    tax_rate_bps: quote.tax_rate_bps,
    tax_cents: quote.tax_cents,
    total_cents: quote.total_cents,
    first_name: contact.first_name,
    ship_city: shipTo?.city ?? null,
    ship_state: shipTo?.state ?? null,
    payment_instructions: "Transfer to account 000 (example). Use your order number as the reference.",
    pickup_note: body.fulfillment === "pickup" ? "Studio, Tuesday to Saturday" : null,
    amount_paid_cents: 0,
    balance_cents: quote.total_cents,
  };
}

/**
 * A fake API with checkout: quotes priced by priceCart, orders answered by `onOrder` (default:
 * created), tracking for the last placed order. `orders` records every order body sent.
 */
export function checkoutServer({ store = OPEN_STORE, onOrder }: { store?: Schemas["PublicStoreOut"]; onOrder?: (body: Record<string, unknown>, n: number) => Response | undefined | Promise<Response | undefined> } = {}) {
  const orders: Record<string, unknown>[] = [];
  const quotes: { items: CartItem[]; fulfillment: string | null }[] = [];
  let placed: Order | null = null;

  const routes: Record<string, Responder> = {
    "/api/v1/public/checkout/quote": async (_url, request) => {
      const body = (await request.json()) as { items: CartItem[]; fulfillment: string | null };
      quotes.push(body);
      return json(200, priceCart(body.items, body.fulfillment ?? null));
    },
    "/api/v1/public/orders": async (_url, request) => {
      const body = (await request.json()) as Record<string, unknown>;
      orders.push(body);
      const custom = await onOrder?.(body, orders.length);
      if (custom) return custom;
      const quote = priceCart(body.items as CartItem[], body.fulfillment as string);
      placed = orderFrom(body, quote);
      return json(201, placed);
    },
    [`/api/v1/public/orders/${TOKEN}`]: () => (placed ? json(200, placed) : json(404, { detail: "Order not found" })),
  };
  const fetch = fakeApi({ store, products: CATALOG, routes });
  return { fetch, orders, quotes, setPlaced: (order: Order) => (placed = order) };
}
