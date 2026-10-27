import type { Schemas } from "@delea/api-client";
import { vi } from "vitest";
import { makeCustomer } from "../customers/testServer";
import { DEFAULT_BRAND_RESPONSE, jsonResponse, TENANT_A } from "../test/harness";

type Product = Schemas["ProductOut"];
type Customer = Schemas["CustomerOut"];
type Invoice = Schemas["InvoiceOut"];
type Preview = Schemas["InvoicePreviewOut"];
type Item = Schemas["LineItemIn"];

export { makeCustomer };

let counter = 0;
const next = () => String((counter += 1)).padStart(12, "0");

export function makeProduct(overrides: Partial<Product> = {}): Product {
  const n = next();
  return {
    id: `p0000000-0000-0000-0000-${n}`,
    tenant_id: TENANT_A,
    sku: `DL-${n.slice(-3)}`,
    name: `Product ${n.slice(-3)}`,
    description: null,
    metal: "silver_925",
    price_cents: 10000,
    sale_price_cents: null,
    sale_ends_at: null,
    stock_qty: 10,
    low_stock_threshold: 2,
    icon: null,
    is_published: true,
    sort_order: 0,
    made_to_order: false,
    lead_time_note: null,
    personalization: [],
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    images: [],
    ...overrides,
  };
}

export function makeInvoice(overrides: Partial<Invoice> = {}): Invoice {
  const n = next();
  const invoice: Invoice = {
    id: `i0000000-0000-0000-0000-${n}`,
    tenant_id: TENANT_A,
    quote_number: `QTE-2026-${n.slice(-3)}`,
    invoice_number: null,
    order_status: "quote",
    customer_id: null,
    customer_name: "Walk-in customer",
    source: "admin",
    subtotal_cents: 10000,
    tax_rate_bps: 800,
    tax_cents: 800,
    total_cents: 10800,
    due_date: null,
    notes: null,
    payment_method: null,
    paid_at: null,
    refunded_at: null,
    cancelled_at: null,
    created_at: "2026-10-20T15:00:00Z",
    updated_at: "2026-10-20T15:00:00Z",
    shipping_cents: 0,
    fulfillment: null,
    payment_choice: null,
    order_ref: null,
    amount_paid_cents: 0,
    balance_cents: 0,
    production_started_at: null,
    stock_short: false,
    items: [],
    payments: [],
    ...overrides,
  };
  // The ledger follows the total unless the test says otherwise: a quote owes it all.
  if (overrides.balance_cents === undefined) invoice.balance_cents = invoice.total_cents - invoice.amount_paid_cents;
  return invoice;
}

/** A paid invoice (number, paid_at, method) at the given status. */
export function paidInvoice(overrides: Partial<Invoice> = {}): Invoice {
  const total = overrides.total_cents ?? 10800;
  const invoice = makeInvoice({
    order_status: "paid",
    payment_method: "card",
    paid_at: "2026-10-20T15:05:00Z",
    amount_paid_cents: total,
    ...overrides,
  });
  return { ...invoice, invoice_number: invoice.invoice_number ?? invoice.quote_number?.replace("QTE", "INV") ?? null };
}

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
  authorization: string | null;
}

const ORDER = ["quote", "paid", "in_production", "shipped", "delivered"] as const;
const halfUp = (amount: number, bps: number) => Math.floor((amount * bps + 5000) / 10000);

const STATUS_WORDS: Record<string, string> = { in_production: "in production" };

/**
 * An in-memory invoices API with the backend's pricing (backend/app/services/pricing.py: sale
 * price, wholesale minimum without stacking, half-up tax on the subtotal, tax-exempt pays 0),
 * the status rules, payment methods, closed invoices, stock, and the role rules for the token's
 * app_role. Products and customers are read-only here.
 */
export function invoiceServer({
  products = [],
  customers = [],
  invoices = [],
  taxRateBps = 800,
}: { products?: Product[]; customers?: Customer[]; invoices?: Invoice[]; taxRateBps?: number } = {}) {
  const productMap = new Map(products.map((p) => [p.id, structuredClone(p)]));
  const customerMap = new Map(customers.map((c) => [c.id, structuredClone(c)]));
  const invoiceMap = new Map(invoices.map((i) => [i.id, structuredClone(i)]));
  const calls: Call[] = [];
  type Responder = (call: Call) => Response | undefined | Promise<Response | undefined>;
  const overrides = new Map<string, Responder>();
  const interceptors: Responder[] = [];
  let invoiceNumber = 100;

  const fetch = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    const text = request.method === "GET" || request.method === "DELETE" ? "" : await request.text();
    const call: Call = {
      method: request.method,
      path: url.pathname,
      query: url.searchParams,
      body: text ? JSON.parse(text) : undefined,
      authorization: request.headers.get("Authorization"),
    };
    calls.push(call);
    for (const intercept of interceptors) {
      const response = await intercept(call);
      if (response) return response;
    }
    const custom = await overrides.get(`${call.method} ${call.path}`)?.(call);
    if (custom) return custom;
    return handle(call, roleOf(call.authorization));
  });

  function roleOf(authorization: string | null): string | null {
    const part = authorization?.replace(/^Bearer /, "").split(".")[1];
    if (!part) return null;
    return (JSON.parse(atob(part.replace(/-/g, "+").replace(/_/g, "/"))) as { app_role?: string }).app_role ?? null;
  }

  function price(items: Item[], customer: Customer | undefined, overrideBps: number | null): Preview {
    const lines = items.map((item) => {
      const p = productMap.get(item.product_id)!;
      let unit = p.sale_price_cents ?? p.price_cents;
      if (customer?.customer_type === "wholesale") {
        unit = Math.min(unit, halfUp(p.price_cents, 10000 - customer.wholesale_discount_bps));
      }
      return { product_id: p.id, description: p.name, qty: item.qty, unit_price_cents: unit, line_total_cents: unit * item.qty };
    });
    const subtotal = lines.reduce((sum, l) => sum + l.line_total_cents, 0);
    const rate = customer?.tax_exempt ? 0 : (overrideBps ?? taxRateBps);
    const tax = halfUp(subtotal, rate);
    const needed = new Map<string, number>();
    for (const item of items) needed.set(item.product_id, (needed.get(item.product_id) ?? 0) + item.qty);
    const stock_shortages = [...needed]
      .map(([id, qty]) => ({ product: productMap.get(id)!, qty }))
      .filter(({ product, qty }) => product.stock_qty < qty)
      .map(({ product, qty }) => ({ product_id: product.id, sku: product.sku, available: product.stock_qty, needed: qty }));
    return { lines, subtotal_cents: subtotal, tax_rate_bps: rate, tax_cents: tax, total_cents: subtotal + tax, stock_shortages };
  }

  function withItems(invoice: Invoice, role: string | null): Invoice {
    return role === "cpa" ? { ...invoice, items: null } : invoice;
  }

  /** quote -> later: number, paid_at, stock (or the database's message and nothing changed). */
  function pay(invoice: Invoice): Response | null {
    const needed = new Map<string, number>();
    for (const line of invoice.items ?? []) {
      if (line.product_id) needed.set(line.product_id, (needed.get(line.product_id) ?? 0) + line.qty);
    }
    for (const [id, qty] of needed) {
      const p = productMap.get(id)!;
      if (p.stock_qty < qty) {
        return jsonResponse(409, { detail: `Not enough stock for SKU ${p.sku}: ${p.stock_qty} available, ${qty} needed.` });
      }
    }
    for (const [id, qty] of needed) productMap.get(id)!.stock_qty -= qty;
    invoiceNumber += 1;
    invoice.invoice_number = `INV-2026-${invoiceNumber}`;
    invoice.paid_at = "2026-10-22T16:00:00Z";
    // The backend records a payment for the balance before the move.
    invoice.amount_paid_cents = invoice.total_cents;
    invoice.balance_cents = 0;
    return null;
  }

  function handle({ method, path, query, body }: Call, role: string | null): Response {
    if (path === "/api/v1/settings/currency") return jsonResponse(200, { currency: "USD" });
    if (path === "/api/v1/settings/brand") return jsonResponse(200, DEFAULT_BRAND_RESPONSE);
    const writer = role === "owner" || role === "staff";

    if (path === "/api/v1/products" && method === "GET") {
      const q = query.get("q")?.toLowerCase();
      const rows = [...productMap.values()].filter((p) => !q || p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q));
      return jsonResponse(200, rows.slice(0, Number(query.get("limit") ?? 50)));
    }
    if (path === "/api/v1/customers" && method === "GET") {
      const q = query.get("q")?.toLowerCase();
      const rows = [...customerMap.values()].filter((c) =>
        [c.first_name, c.last_name, c.company_name].some((v) => !q || v?.toLowerCase().includes(q)),
      );
      return jsonResponse(200, rows.slice(0, Number(query.get("limit") ?? 50)));
    }
    const customerMatch = /^\/api\/v1\/customers\/([^/]+)$/.exec(path);
    if (customerMatch) {
      const c = customerMap.get(customerMatch[1]);
      return c ? jsonResponse(200, c) : jsonResponse(404, { detail: "Customer not found" });
    }

    if (path === "/api/v1/invoices" && method === "GET") {
      let rows = [...invoiceMap.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
      if (query.get("status")) rows = rows.filter((i) => i.order_status === query.get("status"));
      if (query.get("customer_id")) rows = rows.filter((i) => i.customer_id === query.get("customer_id"));
      if (query.get("date_from")) rows = rows.filter((i) => i.created_at.slice(0, 10) >= query.get("date_from")!);
      if (query.get("date_to")) rows = rows.filter((i) => i.created_at.slice(0, 10) <= query.get("date_to")!);
      const q = query.get("q")?.toLowerCase();
      if (q) rows = rows.filter((i) => [i.quote_number, i.invoice_number, i.order_ref].some((n) => n?.toLowerCase().includes(q)));
      if (query.get("source")) rows = rows.filter((i) => i.source === query.get("source"));
      if (query.get("awaiting_payment") === "true") rows = rows.filter((i) => i.source === "storefront" && i.order_status === "quote" && !i.cancelled_at);
      const offset = Number(query.get("offset") ?? 0);
      return jsonResponse(200, rows.slice(offset, offset + Number(query.get("limit") ?? 50)).map((i) => ({ ...i, items: null })));
    }
    if (path === "/api/v1/invoices/counts") {
      if (!writer) return jsonResponse(403, { detail: "Insufficient role" });
      const awaiting = [...invoiceMap.values()].filter((i) => i.source === "storefront" && i.order_status === "quote" && !i.cancelled_at);
      return jsonResponse(200, { awaiting_payment_web: awaiting.length });
    }
    if (path === "/api/v1/invoices/export.csv") {
      return new Response("quote_number,invoice_number\n", { status: 200, headers: { "Content-Type": "text/csv" } });
    }
    if (path === "/api/v1/invoices/preview" && method === "POST") {
      if (!writer) return jsonResponse(403, { detail: "Insufficient role" });
      const b = body as { customer_id?: string | null; items: Item[]; tax_rate_bps?: number | null };
      if (b.tax_rate_bps != null && role !== "owner") return jsonResponse(403, { detail: "only the owner can override the tax rate" });
      return jsonResponse(200, price(b.items, b.customer_id ? customerMap.get(b.customer_id) : undefined, b.tax_rate_bps ?? null));
    }
    if (path === "/api/v1/invoices" && method === "POST") {
      if (!writer) return jsonResponse(403, { detail: "Insufficient role" });
      const b = body as Schemas["InvoiceCreate"];
      const customer = b.customer_id ? customerMap.get(b.customer_id) : undefined;
      const priced = price(b.items, customer, b.tax_rate_bps ?? null);
      if (b.pay_now && !b.payment_method) return jsonResponse(422, { detail: "a payment method is required to take payment" });
      const invoice = makeInvoice({
        customer_id: customer?.id ?? null,
        customer_name: customer ? `${customer.first_name} ${customer.last_name ?? ""}`.trim() : "Walk-in customer",
        subtotal_cents: priced.subtotal_cents,
        tax_rate_bps: priced.tax_rate_bps,
        tax_cents: priced.tax_cents,
        total_cents: priced.total_cents,
        notes: b.notes ?? null,
        due_date: b.due_date ?? null,
        items: priced.lines.map((l, n) => ({ id: `line-${n}-${next()}`, ...l })),
      });
      if (b.pay_now) {
        const refused = pay(invoice);
        if (refused) return refused; // nothing kept
        invoice.order_status = "paid";
        invoice.payment_method = b.payment_method ?? null;
      }
      invoiceMap.set(invoice.id, invoice);
      return jsonResponse(201, invoice);
    }

    const match = /^\/api\/v1\/invoices\/([^/]+)(\/[a-z/]+)?$/.exec(path);
    const invoice = match && invoiceMap.get(match[1]);
    if (!invoice) return jsonResponse(404, { detail: "Invoice not found" });
    const action = match[2] ?? "";

    if (action === "" && method === "GET") return jsonResponse(200, withItems(invoice, role));
    if (!writer) return jsonResponse(403, { detail: "Insufficient role" });

    if (action === "/items/preview") {
      if (invoice.order_status !== "quote") return jsonResponse(409, { detail: "only quotes can be edited" });
      const b = body as { items: Item[] };
      return jsonResponse(200, price(b.items, invoice.customer_id ? customerMap.get(invoice.customer_id) : undefined, invoice.tax_rate_bps));
    }
    if (action === "/items" && method === "PUT") {
      if (invoice.order_status !== "quote") return jsonResponse(409, { detail: "only quotes can be edited" });
      const b = body as { items: Item[] };
      const priced = price(b.items, invoice.customer_id ? customerMap.get(invoice.customer_id) : undefined, invoice.tax_rate_bps);
      Object.assign(invoice, {
        subtotal_cents: priced.subtotal_cents,
        tax_cents: priced.tax_cents,
        total_cents: priced.total_cents,
        items: priced.lines.map((l, n) => ({ id: `line-${n}-${next()}`, ...l })),
      });
      return jsonResponse(200, invoice);
    }
    if (action === "/status") {
      const b = body as { status: (typeof ORDER)[number]; payment_method?: string | null };
      if (b.status !== invoice.order_status) {
        if (invoice.cancelled_at) return jsonResponse(409, { detail: "This invoice is cancelled, so its status can't change." });
        if (invoice.refunded_at) return jsonResponse(409, { detail: "This invoice is refunded, so its status can't change." });
      }
      const paying = invoice.order_status === "quote" && b.status !== "quote";
      if (paying && !b.payment_method) return jsonResponse(422, { detail: "a payment method is required to take payment" });
      if (!paying && b.payment_method) {
        return jsonResponse(422, { detail: "the payment method is recorded only when a quote is paid" });
      }
      if (ORDER.indexOf(b.status) < ORDER.indexOf(invoice.order_status)) {
        return jsonResponse(422, { detail: `This invoice can't move back to ${STATUS_WORDS[b.status] ?? b.status}.` });
      }
      if (paying) {
        const refused = pay(invoice);
        if (refused) return refused;
        invoice.payment_method = b.payment_method as Invoice["payment_method"];
      }
      invoice.order_status = b.status;
      return jsonResponse(200, invoice);
    }
    if (action === "" && method === "PATCH") {
      const b = body as Schemas["InvoiceUpdate"];
      if ("payment_method" in b && role !== "owner") return jsonResponse(403, { detail: "only the owner can change the payment method" });
      if ("due_date" in b && invoice.order_status !== "quote") {
        return jsonResponse(409, { detail: "The due date can only change while the invoice is a quote." });
      }
      Object.assign(invoice, b);
      return jsonResponse(200, invoice);
    }
    if (role !== "owner") return jsonResponse(403, { detail: "Insufficient role" });
    if (action === "/refund") {
      if (!invoice.paid_at) return jsonResponse(409, { detail: "only paid invoices can be refunded" });
      if (invoice.refunded_at) return jsonResponse(409, { detail: "this invoice is already refunded" });
      if (invoice.cancelled_at) return jsonResponse(409, { detail: "A cancelled invoice can't be refunded." });
      invoice.refunded_at = "2026-10-22T17:00:00Z";
      return jsonResponse(200, invoice);
    }
    if (action === "/cancel") {
      if (invoice.cancelled_at) return jsonResponse(409, { detail: "this invoice is already cancelled" });
      if (invoice.refunded_at) return jsonResponse(409, { detail: "A refunded invoice can't be cancelled." });
      invoice.cancelled_at = "2026-10-22T17:00:00Z";
      return jsonResponse(200, invoice);
    }
    if (action === "" && method === "DELETE") {
      if (invoice.order_status !== "quote") {
        return jsonResponse(403, { detail: "only quotes can be deleted; cancel or refund this invoice instead" });
      }
      invoiceMap.delete(invoice.id);
      return new Response(null, { status: 204 });
    }
    return jsonResponse(404, { detail: "Not found" });
  }

  return {
    fetch,
    calls,
    products: productMap,
    invoices: invoiceMap,
    /** Answer one method + path yourself (return undefined to fall through). */
    override: (key: string, responder: Responder) => overrides.set(key, responder),
    /** Answer any call that matches (return undefined to fall through). */
    intercept: (responder: Responder) => interceptors.push(responder),
    last: (method: string, path: string) => [...calls].reverse().find((c) => c.method === method && c.path === path),
    all: (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path),
  };
}
