import type { AppRole } from "@delea/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeBackend, renderApp, tokenFor } from "../test/harness";
import { invoiceServer, makeInvoice, makeProduct } from "./testServer";

const RING = makeProduct({ name: "Halo Ring", sku: "DL-HALO", price_cents: 14500, stock_qty: 3 });

const webOrder = makeInvoice({
  quote_number: "QTE-2026-050",
  customer_id: "c0000000-0000-4000-8000-000000000050",
  customer_name: "Priya Nair",
  source: "storefront",
  order_ref: "W-7K3P9QAB",
  fulfillment: "ship",
  payment_choice: "bank_transfer",
  subtotal_cents: 14500,
  tax_cents: 1160,
  shipping_cents: 600,
  total_cents: 16260,
  items: [
    {
      id: "line-1",
      product_id: RING.id,
      description: "Halo Ring",
      qty: 1,
      unit_price_cents: 14500,
      line_total_cents: 14500,
      personalization: [
        { label: "Ring size", value: "7" },
        { label: "Engraving", value: "K&J" },
      ],
    },
  ],
  contact: {
    first_name: "Priya",
    last_name: "Nair",
    email: "priya@example.com",
    phone: "555-0142",
    ship_line1: "9 Elm St",
    ship_line2: null,
    ship_city: "Austin",
    ship_state: "TX",
    ship_postal_code: "78701",
    ship_country: "US",
    note: "Gift wrap, please",
  },
});
const cancelledWeb = makeInvoice({ source: "storefront", order_ref: "W-CANCE11D", fulfillment: "pickup", payment_choice: "pay_on_pickup", cancelled_at: "2026-10-21T00:00:00Z" });
const adminQuote = makeInvoice({ quote_number: "QTE-2026-049", customer_name: "Ava Thompson" });

function setup(path: string, role: AppRole = "owner", invoices = [webOrder, cancelledWeb, adminQuote]) {
  const server = invoiceServer({ products: [RING], customers: [], invoices });
  const utils = renderApp({ path, fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { ...utils, server, user: userEvent.setup() };
}

describe("website orders in the invoices list", () => {
  it("tags them Website with their W- reference; admin ones are tagged Admin", async () => {
    setup("/invoices");
    const row = (await screen.findByRole("link", { name: "QTE-2026-050" })).closest("tr")!;
    expect(within(row).getByText("Website")).toBeInTheDocument();
    expect(within(row).getByText("W-7K3P9QAB")).toBeInTheDocument();
    expect(within(row).getByText("Awaiting payment")).toBeInTheDocument();
    const admin = screen.getByRole("link", { name: "QTE-2026-049" }).closest("tr")!;
    expect(within(admin).getByText("Admin")).toBeInTheDocument();
  });

  it("filters by source", async () => {
    const { server, user } = setup("/invoices");
    await user.selectOptions(await screen.findByLabelText("Source"), "storefront");
    await waitFor(() => expect(server.last("GET", "/api/v1/invoices")!.query.get("source")).toBe("storefront"));
    await waitFor(() => expect(screen.queryByRole("link", { name: "QTE-2026-049" })).not.toBeInTheDocument());
  });

  it("the Awaiting payment card counts open website orders and shows them", async () => {
    const { server, user } = setup("/invoices");
    const card = within(await screen.findByRole("region", { name: "Website orders" }));
    expect(await card.findByText("1")).toBeInTheDocument(); // the cancelled one doesn't count
    await user.click(card.getByRole("button", { name: "Show them" }));
    await waitFor(() => expect(server.last("GET", "/api/v1/invoices")!.query.get("awaiting_payment")).toBe("true"));
    expect(screen.getByText("Awaiting payment", { selector: ".filter-chip" })).toBeInTheDocument();
  });

  it("the CPA sees no card and no badge", async () => {
    setup("/invoices", "cpa");
    await screen.findByRole("link", { name: "QTE-2026-050" });
    expect(screen.queryByRole("region", { name: "Website orders" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/awaiting payment/)).not.toBeInTheDocument();
  });
});

describe("the Invoices nav badge", () => {
  it.each(["owner", "staff"] as const)("shows the count to the %s", async (role) => {
    setup("/invoices", role);
    expect(await screen.findByLabelText("1 website order awaiting payment")).toHaveTextContent("1");
  });

  it("is hidden when nothing is waiting", async () => {
    setup("/invoices", "owner", [adminQuote]);
    await screen.findByRole("link", { name: "QTE-2026-049" });
    expect(screen.queryByLabelText(/awaiting payment/)).not.toBeInTheDocument();
  });

  it("doesn't count an order holding a deposit: it shows its own tag and balance", async () => {
    const deposit = { ...webOrder, amount_paid_cents: 5000, balance_cents: webOrder.total_cents - 5000 };
    setup("/invoices", "owner", [deposit, adminQuote]);
    const row = within((await screen.findByRole("link", { name: "QTE-2026-050" })).closest("tr")!);
    expect(await row.findByText("Deposit paid · $112.60 due")).toBeInTheDocument();
    expect(row.queryByText("Awaiting payment")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/awaiting payment/)).not.toBeInTheDocument();
  });
});

describe("a website order's page", () => {
  it("shows the shopper, the ship-to, the answers and shipping", async () => {
    setup(`/invoices/${webOrder.id}`);
    const shopper = within(await screen.findByRole("region", { name: "Shopper" }));
    expect(shopper.getByText("priya@example.com")).toHaveAttribute("href", "mailto:priya@example.com");
    expect(shopper.getByText(/9 Elm St/)).toBeInTheDocument();
    expect(shopper.getByText(/Austin, TX 78701/)).toBeInTheDocument();
    expect(shopper.getByText("Bank transfer")).toBeInTheDocument();
    expect(shopper.getByText("Gift wrap, please")).toBeInTheDocument();
    const answers = within(screen.getByRole("list", { name: "Personalization for Halo Ring" }));
    expect(answers.getByText("Ring size:")).toBeInTheDocument();
    expect(answers.getByText(/K&J/)).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector('dl[aria-label="Totals"]')).not.toBeNull());
    const totals = document.querySelector<HTMLElement>('dl[aria-label="Totals"]')!;
    expect(within(totals).getByText("Shipping")).toBeInTheDocument();
    expect(within(totals).getByText("$6.00")).toBeInTheDocument();
    expect(within(totals).getByText("$162.60")).toBeInTheDocument();
    expect(screen.getByText("W-7K3P9QAB")).toBeInTheDocument();
  });

  it("can't have its lines edited or be deleted; payment is confirmed with the shopper's method", async () => {
    const { server, user } = setup(`/invoices/${webOrder.id}`);
    await screen.findByRole("region", { name: "Shopper" });
    expect(screen.queryByRole("link", { name: "Edit lines" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete quote" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm payment" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText("Confirm the payment for W-7K3P9QAB?")).toBeInTheDocument();
    expect(dialog.getByRole("radio", { name: "Bank transfer" })).toBeChecked();
    await user.click(dialog.getByRole("button", { name: "Confirm payment" }));
    await waitFor(() =>
      expect(server.last("POST", `/api/v1/invoices/${webOrder.id}/status`)?.body).toEqual({ status: "paid", payment_method: "bank_transfer" }),
    );
  });
});
