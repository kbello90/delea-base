import type { AppRole } from "@delea/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";
import { invoiceServer, makeCustomer, makeInvoice, makeProduct, paidInvoice } from "./testServer";

const RING = makeProduct({ name: "Heart Ring", sku: "DL-RING", price_cents: 10000, sale_price_cents: 9000, stock_qty: 5 });
const CHARM = makeProduct({ name: "Star Charm", sku: "DL-CHARM", price_cents: 333, stock_qty: 20 });
const BOX = makeProduct({ name: "Gift Box", sku: "DL-BOX", price_cents: 1500, stock_qty: 1, is_published: false });
const WILLOW = makeCustomer({
  customer_type: "wholesale",
  first_name: "Mia",
  last_name: "Hart",
  company_name: "Willow & Thread",
  email: "orders@willow.example.com",
  wholesale_discount_bps: 1500,
  payment_terms: "net_30",
});
const AVA = makeCustomer({ first_name: "Ava", last_name: "Thompson", email: "ava@example.com" });

function setup(
  path: string,
  { role = "owner", ...data }: { role?: AppRole } & Parameters<typeof invoiceServer>[0] = {},
) {
  const server = invoiceServer({ products: [RING, CHARM, BOX], customers: [WILLOW, AVA], ...data });
  const utils = renderApp({ path, fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { ...utils, server, user: userEvent.setup() };
}

const lastList = (server: ReturnType<typeof invoiceServer>) => server.last("GET", "/api/v1/invoices")!.query;

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------
describe("invoices list", () => {
  const quote = makeInvoice({ quote_number: "QTE-2026-041", customer_id: AVA.id, customer_name: "Ava Thompson", total_cents: 24840 });
  const walkIn = paidInvoice({
    quote_number: "QTE-2026-040",
    invoice_number: "INV-2026-088",
    order_status: "delivered",
    refunded_at: "2026-10-21T10:00:00Z",
    total_cents: 123456,
    created_at: "2026-10-19T09:00:00Z",
  });
  const cancelled = paidInvoice({ invoice_number: "INV-2026-087", cancelled_at: "2026-10-18T10:00:00Z", created_at: "2026-10-18T09:00:00Z" });

  it("shows number, customer or Walk-in, UTC date, status tags and the total", async () => {
    setup("/invoices", { invoices: [quote, walkIn, cancelled] });
    const quoteRow = within((await screen.findByRole("link", { name: "QTE-2026-041" })).closest("tr")!);
    expect(quoteRow.getByText("Ava Thompson")).toBeInTheDocument();
    expect(quoteRow.getByText("Quote")).toBeInTheDocument();
    expect(quoteRow.getByText("$248.40")).toBeInTheDocument();
    expect(quoteRow.getByText("Oct 20, 2026")).toBeInTheDocument();

    const paidRow = within(screen.getByRole("link", { name: "INV-2026-088" }).closest("tr")!);
    expect(paidRow.getByText("Walk-in")).toBeInTheDocument();
    expect(paidRow.getByText("QTE-2026-040")).toBeInTheDocument();
    expect(paidRow.getByText("Delivered")).toBeInTheDocument();
    expect(paidRow.getByText("Refunded")).toBeInTheDocument();
    expect(paidRow.getByText("$1,234.56")).toBeInTheDocument();
    const cancelledRow = within(screen.getByRole("link", { name: "INV-2026-087" }).closest("tr")!);
    expect(cancelledRow.getByText("Cancelled")).toBeInTheDocument();
  });

  it("status, dates and number search are kept in the URL and sent to the API", async () => {
    const { server, location, user } = setup("/invoices", { invoices: [quote, walkIn] });
    await screen.findByRole("link", { name: "QTE-2026-041" });

    await user.selectOptions(screen.getByLabelText("Status"), "delivered");
    await waitFor(() => expect(lastList(server).get("status")).toBe("delivered"));
    expect(location.search).toContain("status=delivered");
    expect(await screen.findByRole("link", { name: "INV-2026-088" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "QTE-2026-041" })).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Created from (UTC)"), "2026-10-01");
    await user.type(screen.getByLabelText("Created to (UTC)"), "2026-10-31");
    await waitFor(() => expect(lastList(server).get("date_to")).toBe("2026-10-31"));
    expect(lastList(server).get("date_from")).toBe("2026-10-01");
    expect(location.search).toContain("from=2026-10-01");

    await user.type(screen.getByLabelText("Search"), "INV-2026-088");
    await waitFor(() => expect(lastList(server).get("q")).toBe("INV-2026-088"));

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(location.search).toBe(""));
  });

  it("the customer filter puts only the customer's id in the URL", async () => {
    const { server, location, user } = setup("/invoices", {
      invoices: [quote, makeInvoice({ quote_number: "QTE-2026-050", customer_id: WILLOW.id, customer_name: "Willow & Thread" })],
    });
    await screen.findByRole("link", { name: "QTE-2026-041" });
    await user.click(screen.getByRole("button", { name: "Change customer" }));
    await user.type(screen.getByLabelText("Find a customer"), "willow");
    await user.click(await screen.findByRole("button", { name: /Willow & Thread/ }));

    await waitFor(() => expect(lastList(server).get("customer_id")).toBe(WILLOW.id));
    expect(location.search).toBe(`?customer=${WILLOW.id}`);
    expect(await screen.findByRole("link", { name: "QTE-2026-050" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "QTE-2026-041" })).not.toBeInTheDocument();
    // The name shows on the page, never in a URL.
    expect(screen.getAllByText("Willow & Thread").length).toBeGreaterThan(0);
    for (const call of server.calls) expect(decodeURIComponent(call.query.toString())).not.toMatch(/Willow|orders@/);
  });

  it("an email typed into the customer search is never sent", async () => {
    const { server, user } = setup("/invoices", { invoices: [quote] });
    await screen.findByRole("link", { name: "QTE-2026-041" });
    await user.click(screen.getByRole("button", { name: "Change customer" }));
    await user.type(screen.getByLabelText("Find a customer"), "ava@example.com");
    expect(await screen.findByText(/email addresses and phone numbers aren't searched/)).toBeInTheDocument();
    // Partial names may be searched while typing; nothing with the "@" ever is.
    expect(server.all("GET", "/api/v1/customers").filter((c) => c.query.get("q")?.includes("@"))).toEqual([]);
  });

  it("pages through 25 at a time", async () => {
    const many = Array.from({ length: 30 }, (_, n) =>
      makeInvoice({ quote_number: `QTE-2026-${String(n + 1).padStart(3, "0")}`, created_at: `2026-10-${String(n + 1).padStart(2, "0")}T00:00:00Z` }),
    );
    const { server, location, user } = setup("/invoices", { invoices: many.slice(0, 26) });
    await screen.findByRole("link", { name: "QTE-2026-026" });
    expect(lastList(server).get("limit")).toBe("26");
    expect(screen.getAllByRole("row")).toHaveLength(26); // header + 25
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(lastList(server).get("offset")).toBe("25"));
    expect(location.search).toBe("?page=2");
    expect(await screen.findByRole("link", { name: "QTE-2026-001" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("shows an empty state and a load error with retry", async () => {
    const { server, user } = setup("/invoices");
    expect(await screen.findByText("No invoices yet")).toBeInTheDocument();
    server.override("GET /api/v1/invoices", () => jsonResponse(503, { detail: "The service is temporarily unavailable." }));
    await user.selectOptions(screen.getByLabelText("Status"), "paid");
    expect(await screen.findByText("Invoices couldn't be loaded")).toBeInTheDocument();
    expect(screen.getByText("The service is temporarily unavailable.")).toBeInTheDocument();
  });

  describe("CSV export", () => {
    let created: Blob[] = [];
    beforeEach(() => {
      created = [];
      URL.createObjectURL = vi.fn((blob: Blob) => {
        created.push(blob);
        return "blob:invoices";
      });
      URL.revokeObjectURL = vi.fn();
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    });
    afterEach(() => vi.restoreAllMocks());

    it("downloads through an authorised request with the current filters (cpa too)", async () => {
      const { server, user } = setup("/invoices?status=paid&from=2026-10-01", { role: "cpa", invoices: [walkIn] });
      await screen.findByRole("heading", { level: 1, name: "Invoices" });
      await user.click(screen.getByRole("button", { name: "Export CSV" }));
      await waitFor(() => expect(created).toHaveLength(1));
      const call = server.last("GET", "/api/v1/invoices/export.csv")!;
      expect(call.authorization).toMatch(/^Bearer /);
      expect(call.query.get("status")).toBe("paid");
      expect(call.query.get("date_from")).toBe("2026-10-01");
      expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
    });
  });

  it("cpa sees the list and export, but no sale, quote or customer filter", async () => {
    setup("/invoices", { role: "cpa", invoices: [walkIn] });
    expect(await screen.findByRole("link", { name: "INV-2026-088" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "New sale" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "New quote" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change customer" })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Record page: actions per role and state
// ---------------------------------------------------------------------------
const CONTROLS = [
  "Mark paid",
  "Mark in production",
  "Mark shipped",
  "Mark delivered",
  "Refund",
  "Cancel invoice",
  "Delete quote",
  "Edit lines",
  "Edit notes",
  "Change due date",
  "Change payment method",
] as const;
type Control = (typeof CONTROLS)[number];

const AT = "2026-10-21T00:00:00Z";
const STATES = {
  quote: () => makeInvoice(),
  paid: () => paidInvoice(),
  in_production: () => paidInvoice({ order_status: "in_production" }),
  shipped: () => paidInvoice({ order_status: "shipped" }),
  delivered: () => paidInvoice({ order_status: "delivered" }),
  refunded: () => paidInvoice({ order_status: "delivered", refunded_at: AT }),
  cancelled: () => paidInvoice({ cancelled_at: AT }),
  cancelled_quote: () => makeInvoice({ cancelled_at: AT }),
};

// Written out (not derived from invoiceActions) so a change to the rules must change this too.
const OWNER: Record<keyof typeof STATES, Control[]> = {
  quote: ["Mark paid", "Cancel invoice", "Delete quote", "Edit lines", "Edit notes", "Change due date"],
  paid: ["Mark in production", "Mark shipped", "Mark delivered", "Refund", "Cancel invoice", "Edit notes", "Change payment method"],
  in_production: ["Mark shipped", "Mark delivered", "Refund", "Cancel invoice", "Edit notes", "Change payment method"],
  shipped: ["Mark delivered", "Refund", "Cancel invoice", "Edit notes", "Change payment method"],
  delivered: ["Refund", "Cancel invoice", "Edit notes", "Change payment method"],
  refunded: ["Edit notes", "Change payment method"],
  cancelled: ["Edit notes", "Change payment method"],
  cancelled_quote: ["Delete quote", "Edit notes", "Change due date"],
};
const STAFF: Record<keyof typeof STATES, Control[]> = {
  quote: ["Mark paid", "Edit lines", "Edit notes", "Change due date"],
  paid: ["Mark in production", "Mark shipped", "Mark delivered", "Edit notes"],
  in_production: ["Mark shipped", "Mark delivered", "Edit notes"],
  shipped: ["Mark delivered", "Edit notes"],
  delivered: ["Edit notes"],
  refunded: ["Edit notes"],
  cancelled: ["Edit notes"],
  cancelled_quote: ["Edit notes", "Change due date"],
};
const EXPECTED: Record<AppRole, Record<keyof typeof STATES, Control[]>> = {
  owner: OWNER,
  staff: STAFF,
  cpa: Object.fromEntries(Object.keys(STATES).map((s) => [s, []])) as unknown as Record<keyof typeof STATES, Control[]>,
};

const matrix = (["owner", "staff", "cpa"] as const).flatMap((role) =>
  (Object.keys(STATES) as (keyof typeof STATES)[]).map((state) => ({ role, state })),
);

describe("invoice record page", () => {
  it.each(matrix)("$role on a $state invoice sees exactly its actions", async ({ role, state }) => {
    const invoice = STATES[state]();
    setup(`/invoices/${invoice.id}`, { role, invoices: [invoice] });
    await screen.findByRole("heading", { level: 2, name: "Items" });
    const shown = CONTROLS.filter((name) => screen.queryByRole(name === "Edit lines" ? "link" : "button", { name }));
    expect(shown).toEqual(EXPECTED[role][state]);
  });

  it("cpa sees the record without line items or a customer link", async () => {
    const invoice = paidInvoice({
      customer_id: AVA.id,
      customer_name: "Ava Thompson",
      items: [{ id: "l1", product_id: RING.id, description: "Heart Ring", qty: 1, unit_price_cents: 9000, line_total_cents: 9000 }],
    });
    setup(`/invoices/${invoice.id}`, { role: "cpa", invoices: [invoice] });
    expect(await screen.findByText("Line items aren't visible to your role.")).toBeInTheDocument();
    expect(screen.queryByText("Heart Ring")).not.toBeInTheDocument();
    expect(screen.getByText("Ava Thompson")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Ava Thompson" })).not.toBeInTheDocument();
    expect(screen.getByText("Card")).toBeInTheDocument();
  });

  it("Mark paid asks for the payment method and sends it", async () => {
    const quote = makeInvoice({
      items: [{ id: "l1", product_id: RING.id, description: "Heart Ring", qty: 2, unit_price_cents: 9000, line_total_cents: 18000 }],
    });
    const { server, user } = setup(`/invoices/${quote.id}`, { role: "staff", invoices: [quote] });
    await user.click(await screen.findByRole("button", { name: "Mark paid" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Mark paid" });
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByLabelText("Cash"));
    await user.click(confirm);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(server.last("POST", `/api/v1/invoices/${quote.id}/status`)!.body).toEqual({ status: "paid", payment_method: "cash" });
    expect(await screen.findByRole("heading", { level: 1, name: "INV-2026-101" })).toBeInTheDocument();
    expect(screen.getByText("Cash")).toBeInTheDocument();
  });

  it("shows the server's stock message verbatim and keeps the quote", async () => {
    const quote = makeInvoice({
      items: [{ id: "l1", product_id: BOX.id, description: "Gift Box", qty: 3, unit_price_cents: 1500, line_total_cents: 4500 }],
    });
    const { user } = setup(`/invoices/${quote.id}`, { invoices: [quote] });
    await user.click(await screen.findByRole("button", { name: "Mark paid" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Card"));
    await user.click(within(dialog).getByRole("button", { name: "Mark paid" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Not enough stock for SKU DL-BOX: 1 available, 3 needed.");
    expect(screen.getByRole("heading", { level: 1, hidden: true })).toHaveTextContent(quote.quote_number!);
  });

  it("a forward move confirms first, and the owner's refund error is shown verbatim", async () => {
    const invoice = paidInvoice();
    const { server, user } = setup(`/invoices/${invoice.id}`, { invoices: [invoice] });
    await user.click(await screen.findByRole("button", { name: "Mark shipped" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Mark shipped" }));
    await waitFor(() => expect(server.last("POST", `/api/v1/invoices/${invoice.id}/status`)!.body).toEqual({ status: "shipped", payment_method: null }));
    expect(await screen.findByText("Shipped")).toBeInTheDocument();

    server.override(`POST /api/v1/invoices/${invoice.id}/refund`, () => jsonResponse(409, { detail: "A cancelled invoice can't be refunded." }));
    await user.click(screen.getByRole("button", { name: "Refund" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Refund" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("A cancelled invoice can't be refunded.");
  });

  it("staff edit notes on a paid invoice; the due date is editable only on a quote", async () => {
    const invoice = paidInvoice({ notes: "Gift wrap" });
    const { server, user } = setup(`/invoices/${invoice.id}`, { role: "staff", invoices: [invoice] });
    await user.click(await screen.findByRole("button", { name: "Edit notes" }));
    const notes = screen.getByRole("textbox", { name: "Notes" });
    await user.clear(notes);
    await user.type(notes, "Gift wrap, gold ribbon");
    await user.click(screen.getByRole("button", { name: "Save notes" }));
    expect(await screen.findByText("Gift wrap, gold ribbon")).toBeInTheDocument();
    expect(server.last("PATCH", `/api/v1/invoices/${invoice.id}`)!.body).toEqual({ notes: "Gift wrap, gold ribbon" });
    expect(screen.queryByRole("button", { name: "Change due date" })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Quote form
// ---------------------------------------------------------------------------
async function addProducts(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Add Heart Ring" }));
  await user.click(screen.getByRole("button", { name: "Add Star Charm" }));
  await user.click(screen.getByRole("button", { name: "One more Star Charm" }));
  await user.click(screen.getByRole("button", { name: "One more Star Charm" }));
}

describe("new quote", () => {
  it("shows only the server's prices: wholesale minimum and half-up tax", async () => {
    const { server, user } = setup("/invoices/new");
    await user.click(await screen.findByRole("button", { name: "Change customer" }));
    await user.type(screen.getByLabelText("Find a customer"), "willow");
    await user.click(await screen.findByRole("button", { name: /Willow & Thread/ }));
    await addProducts(user);

    // Ring: the lower of sale 90.00 and 15% off 100.00 = 85.00. Charm: 15% off 3.33 = 2.8305 -> 2.83,
    // x 3 = 8.49. Subtotal 93.49, tax 8% = 7.4792 -> 7.48 (half up), total 100.97.
    await screen.findByText("$100.97"); // the final cart priced (earlier carts priced on the way)
    const totals = screen.getByLabelText("Totals");
    expect(within(totals).getByText("$93.49")).toBeInTheDocument();
    expect(within(totals).getByText("$7.48")).toBeInTheDocument();
    expect(within(totals).getByText("$100.97")).toBeInTheDocument();
    const charm = within(screen.getByText("Star Charm", { selector: ".cell-title" }).closest("tr")!);
    expect(charm.getByText("$2.83")).toBeInTheDocument();
    expect(charm.getByText("$8.49")).toBeInTheDocument();
    const ring = within(screen.getByText("Heart Ring", { selector: ".cell-title" }).closest("tr")!);
    expect(ring.getAllByText("$85.00")).toHaveLength(2);
    expect(server.last("POST", "/api/v1/invoices/preview")!.body).toEqual({
      customer_id: WILLOW.id,
      items: [
        { product_id: RING.id, qty: 1 },
        { product_id: CHARM.id, qty: 3 },
      ],
      tax_rate_bps: null,
    });
  });

  it("a changed cart has no total until the server has priced it again", async () => {
    const { server, user } = setup("/invoices/new");
    await addProducts(user);
    expect(within(await screen.findByLabelText("Totals")).getByText(/^\$/, { selector: ".totals__total dd" })).toBeInTheDocument();
    await screen.findByText("$107.99"); // 90.00 + 9.99 = 99.99, tax 8.00

    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    server.override("POST /api/v1/invoices/preview", async () => {
      await held;
      return undefined;
    });
    await user.click(screen.getByRole("button", { name: "One more Heart Ring" }));
    expect(await screen.findAllByText("Pricing…")).not.toHaveLength(0);
    expect(screen.queryByLabelText("Totals")).not.toBeInTheDocument();
    expect(screen.queryByText("$107.99")).not.toBeInTheDocument();
    release();
    expect(within(await screen.findByLabelText("Totals")).getByText("$205.19")).toBeInTheDocument(); // 189.99 + 15.20
  });

  it("only the owner can override the tax rate", async () => {
    const owner = setup("/invoices/new");
    await owner.user.type(await screen.findByLabelText("Tax rate override (%)"), "5");
    await owner.user.click(await screen.findByRole("button", { name: "Add Heart Ring" }));
    await waitFor(() => expect(owner.server.last("POST", "/api/v1/invoices/preview")!.body).toMatchObject({ tax_rate_bps: 500 }));
    expect(await screen.findByText("Tax (5%)")).toBeInTheDocument();
    owner.unmount();

    setup("/invoices/new", { role: "staff" });
    await screen.findByRole("button", { name: "Add Heart Ring" });
    expect(screen.queryByLabelText("Tax rate override (%)")).not.toBeInTheDocument();
  });

  it("saves a walk-in quote with products and quantities only, then opens it", async () => {
    const { server, location, user } = setup("/invoices/new", { role: "staff" });
    await addProducts(user);
    await user.type(screen.getByRole("textbox", { name: "Notes" }), "Engrave M");
    await screen.findByLabelText("Totals");
    await user.click(screen.getByRole("button", { name: "Save quote" }));
    await waitFor(() => expect(location.pathname).toMatch(/^\/invoices\/i0/));
    expect(server.last("POST", "/api/v1/invoices")!.body).toEqual({
      customer_id: null,
      items: [
        { product_id: RING.id, qty: 1 },
        { product_id: CHARM.id, qty: 3 },
      ],
      tax_rate_bps: null,
      notes: "Engrave M",
      due_date: null,
      pay_now: false,
    });
    expect(await screen.findByText("Walk-in")).toBeInTheDocument();
  });

  it("drafts are listed with a Draft tag", async () => {
    setup("/invoices/new");
    const box = await screen.findByRole("button", { name: "Add Gift Box" });
    expect(within(box).getByText("Draft")).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: "Add Heart Ring" })).queryByText("Draft")).not.toBeInTheDocument();
  });
});

describe("edit quote lines", () => {
  it("a paid invoice has no Edit lines", async () => {
    const invoice = paidInvoice({ invoice_number: "INV-2026-090" });
    setup(`/invoices/${invoice.id}`, { invoices: [invoice] });
    await screen.findByRole("heading", { level: 1, name: "INV-2026-090" });
    expect(screen.queryByRole("link", { name: "Edit lines" })).not.toBeInTheDocument();
  });

  it("the edit page of a paid invoice refuses", async () => {
    const invoice = paidInvoice({ invoice_number: "INV-2026-090" });
    setup(`/invoices/${invoice.id}/edit`, { invoices: [invoice] });
    expect(await screen.findByText("Only quotes can be edited. INV-2026-090 is paid, so its lines can't change.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save lines" })).not.toBeInTheDocument();
  });

  it("re-prices a quote's new lines with its own preview and saves them", async () => {
    const quote = makeInvoice({
      items: [{ id: "l1", product_id: RING.id, description: "Heart Ring", qty: 1, unit_price_cents: 9000, line_total_cents: 9000 }],
    });
    const { server, location, user } = setup(`/invoices/${quote.id}/edit`, { role: "staff", invoices: [quote] });
    await user.click(await screen.findByRole("button", { name: "One more Heart Ring" }));
    expect(within(await screen.findByLabelText("Totals")).getByText("$194.40")).toBeInTheDocument();
    expect(server.last("POST", `/api/v1/invoices/${quote.id}/items/preview`)!.body).toEqual({ items: [{ product_id: RING.id, qty: 2 }] });
    await user.click(screen.getByRole("button", { name: "Save lines" }));
    await waitFor(() => expect(location.pathname).toBe(`/invoices/${quote.id}`));
    expect(server.last("PUT", `/api/v1/invoices/${quote.id}/items`)!.body).toEqual({ items: [{ product_id: RING.id, qty: 2 }] });
  });
});

// ---------------------------------------------------------------------------
// In-store sale
// ---------------------------------------------------------------------------
describe("in-store sale", () => {
  it("search, add, choose the method, take payment: paid, then delivered", async () => {
    const { server, user } = setup("/invoices/sell", { role: "staff" });
    await user.type(await screen.findByLabelText("Find a product"), "ring");
    await user.click(await screen.findByRole("button", { name: "Add Heart Ring" }));
    const take = await screen.findByRole("button", { name: "Take payment · $97.20" });
    expect(take).toBeDisabled(); // no method yet
    await user.click(screen.getByLabelText("Cash"));
    await user.click(take);

    expect(await screen.findByRole("heading", { name: "Paid · INV-2026-101" })).toBeInTheDocument();
    expect(await screen.findByText("Handed over: marked delivered.")).toBeInTheDocument();
    expect(server.last("POST", "/api/v1/invoices")!.body).toEqual({
      customer_id: null,
      items: [{ product_id: RING.id, qty: 1 }],
      pay_now: true,
      payment_method: "cash",
    });
    const created = [...server.invoices.values()][0];
    expect(server.last("POST", `/api/v1/invoices/${created.id}/status`)!.body).toEqual({ status: "delivered", payment_method: null });
    expect(within(screen.getByLabelText("Totals")).getByText("$97.20")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "New sale" }));
    expect(await screen.findByText("No items yet. Add a product above.")).toBeInTheDocument();
  });

  it("if marking delivered fails, the sale stays paid and can be retried", async () => {
    const { server, user } = setup("/invoices/sell");
    let fail = true;
    server.intercept((call) =>
      call.method === "POST" && call.path.endsWith("/status") && fail
        ? jsonResponse(503, { detail: "The service is temporarily unavailable. Try again shortly." })
        : undefined,
    );
    await user.click(await screen.findByRole("button", { name: "Add Heart Ring" }));
    await user.click(screen.getByLabelText("Card"));
    await user.click(await screen.findByRole("button", { name: "Take payment · $97.20" }));
    const alert = await screen.findByText(/The payment is recorded and INV-2026-101 is paid, but it isn't marked delivered yet/);
    expect(alert).toHaveTextContent("The service is temporarily unavailable. Try again shortly.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try marking delivered again" }));
    expect(await screen.findByText("Handed over: marked delivered.")).toBeInTheDocument();
    expect(server.all("POST", "/api/v1/invoices")).toHaveLength(1); // never charged twice
  });

  it("a stock shortage is shown and blocks payment; a refused sale shows the server's message", async () => {
    const { server, user } = setup("/invoices/sell");
    await user.click(await screen.findByRole("button", { name: "Add Gift Box" }));
    await user.click(screen.getByRole("button", { name: "One more Gift Box" }));
    expect(await screen.findByText("Not enough stock for SKU DL-BOX: 1 available, 2 needed.")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Card"));
    expect(screen.getByRole("button", { name: "Take payment" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "One fewer Gift Box" }));
    server.override("POST /api/v1/invoices", () =>
      jsonResponse(409, { detail: "Not enough stock for SKU DL-BOX: 0 available, 1 needed." }),
    );
    await user.click(await screen.findByRole("button", { name: "Take payment · $16.20" }));
    expect(await screen.findByText("The sale wasn't recorded: Not enough stock for SKU DL-BOX: 0 available, 1 needed.")).toBeInTheDocument();
  });

  it("cpa can't open the sale or quote screens", async () => {
    setup("/invoices/sell", { role: "cpa" });
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("You don't have access to this page");
  });
});
