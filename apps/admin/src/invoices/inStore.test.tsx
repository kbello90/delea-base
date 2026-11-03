import type { AppRole } from "@delea/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeBackend, renderApp, tokenFor } from "../test/harness";
import { invoiceServer, makeInvoice, makeProduct } from "./testServer";

// Slice C1: selling at the counter. Barcode scans, personalization answers, deposits, made to
// order, and the In store source.

const RING = makeProduct({ name: "Heart Ring", sku: "DL-RING", price_cents: 10000, stock_qty: 5 });
const RING_XL = makeProduct({ name: "Heart Ring XL", sku: "DL-RING-XL", price_cents: 12000, stock_qty: 5 });
const SIGNET = makeProduct({
  name: "Initial Signet",
  sku: "DL-SIG",
  price_cents: 15000,
  stock_qty: 5,
  personalization: [
    { id: "f-initial", position: 1, label: "Initial", kind: "text", required: true, max_length: 3, choices: null },
    { id: "f-size", position: 2, label: "Size", kind: "choice", required: false, max_length: null, choices: ["6", "7", "8"] },
  ],
});
const BESPOKE = makeProduct({ name: "Bespoke Band", sku: "DL-BESPOKE", price_cents: 40000, stock_qty: 0, made_to_order: true });

function setup(path: string, { role = "staff", ...data }: { role?: AppRole } & Parameters<typeof invoiceServer>[0] = {}) {
  const server = invoiceServer({ products: [RING, RING_XL, SIGNET, BESPOKE], ...data });
  const utils = renderApp({ path, fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { ...utils, server, user: userEvent.setup() };
}

const cartRows = () => within(screen.getByRole("table", { name: "Items" })).getAllByRole("row").slice(1);

describe("barcode scan", () => {
  it("Enter adds one of the product with exactly that SKU, any case, and clears the box", async () => {
    const { server, user } = setup("/invoices/sell");
    const box = await screen.findByLabelText("Find a product");
    await user.type(box, "dl-ring{Enter}");
    expect(await screen.findByText("Added Heart Ring (DL-RING).")).toBeInTheDocument();
    expect(box).toHaveValue("");
    expect(server.calls.find((c) => c.query.get("sku"))!.query.get("sku")).toBe("dl-ring");
    expect(cartRows()).toHaveLength(1);

    await user.type(box, "DL-RING{Enter}"); // a second scan: one more on the same line
    await waitFor(() => expect(screen.getByLabelText("Quantity of Heart Ring")).toHaveValue(2));
    expect(cartRows()).toHaveLength(1);
    expect(screen.queryByText(/Heart Ring XL/, { selector: ".cell-title" })).not.toBeInTheDocument(); // DL-RING is a prefix of DL-RING-XL
  });

  it("a code with no exact match adds nothing and says so", async () => {
    const { user } = setup("/invoices/sell");
    const box = await screen.findByLabelText("Find a product");
    await user.type(box, "DL-RIN{Enter}");
    expect(await screen.findByText("No product with SKU DL-RIN. Nothing was added.")).toBeInTheDocument();
    expect(box).toHaveValue("");
    expect(screen.getByText("No items yet. Add a product above.")).toBeInTheDocument();
  });

  it("typing still searches by name or SKU, any case", async () => {
    const { user } = setup("/invoices/sell");
    await user.type(await screen.findByLabelText("Find a product"), "HEART");
    expect(await screen.findByRole("button", { name: "Add Heart Ring XL" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add Heart Ring" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add Initial Signet" })).not.toBeInTheDocument();
  });
});

describe("personalization at the counter", () => {
  it("each personalized piece is its own line; payment waits for the required answers", async () => {
    const { server, user } = setup("/invoices/sell");
    const box = await screen.findByLabelText("Find a product");
    await user.type(box, "DL-SIG{Enter}");
    await screen.findByText("Added Initial Signet (DL-SIG).");
    await user.type(box, "DL-SIG{Enter}");
    await waitFor(() => expect(screen.getAllByLabelText("Initial")).toHaveLength(2));
    expect(cartRows()).toHaveLength(2);

    await user.click(screen.getByLabelText("Cash"));
    const take = await screen.findByRole("button", { name: /^Take payment/ });
    expect(take).toBeDisabled();
    expect(screen.getByText("Answer the personalization questions in the cart first.")).toBeInTheDocument();
    expect(screen.getAllByText("Required")).toHaveLength(2);

    const [first, second] = screen.getAllByLabelText("Initial");
    await user.type(first, "ABCD");
    expect(screen.getByText("At most 3 characters")).toBeInTheDocument();
    await user.clear(first);
    await user.type(first, "KB");
    await user.selectOptions(screen.getAllByLabelText("Size (optional)")[0], "7");
    await user.type(second, "M");
    await waitFor(() => expect(screen.getByRole("button", { name: "Take payment · $324.00" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Take payment · $324.00" }));

    expect(await screen.findByRole("heading", { name: /^Paid · INV-/ })).toBeInTheDocument();
    expect(server.last("POST", "/api/v1/invoices")!.body).toEqual({
      customer_id: null,
      items: [
        { product_id: SIGNET.id, qty: 1, personalization: { "f-initial": "KB", "f-size": "7" } },
        { product_id: SIGNET.id, qty: 1, personalization: { "f-initial": "M" } },
      ],
      payment_method: "cash",
      source: "in_store",
      pay_now: true,
    });
    expect(screen.getByText("(Initial: KB, Size: 7)")).toBeInTheDocument();
    // Typing answers never re-priced the cart: previews carry no answers.
    for (const call of server.calls.filter((c) => c.path === "/api/v1/invoices/preview")) {
      expect((call.body as { items: { personalization: object }[] }).items.every((i) => Object.keys(i.personalization).length === 0)).toBe(true);
    }
  });
});

describe("deposits", () => {
  it("takes a deposit: a quote holding it, not handed over, with a deposit receipt", async () => {
    const { server, user } = setup("/invoices/sell");
    await user.click(await screen.findByRole("button", { name: "Add Heart Ring" }));
    await user.click(screen.getByLabelText("Take a deposit"));
    await user.click(screen.getByLabelText("Card"));
    const amount = screen.getByLabelText("Deposit amount");

    await user.type(amount, "108");
    expect(await screen.findByText("A deposit is less than the total. To take it all, choose Pay in full.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Take deposit" })).toBeDisabled();

    await user.clear(amount);
    await user.type(amount, "30");
    await user.click(await screen.findByRole("button", { name: "Take deposit · $30.00" }));

    expect(await screen.findByRole("heading", { name: /^Deposit taken · QTE-/ })).toBeInTheDocument();
    expect(screen.getByText(/Deposit \$30\.00 received\. Balance due \$78\.00/)).toBeInTheDocument();
    expect(server.last("POST", "/api/v1/invoices")!.body).toEqual({
      customer_id: null,
      items: [{ product_id: RING.id, qty: 1, personalization: {} }],
      payment_method: "card",
      source: "in_store",
      pay_now: false,
      deposit_cents: 3000,
    });
    expect(server.calls.some((c) => c.path.endsWith("/status"))).toBe(false); // nothing handed over
    expect(screen.getByRole("button", { name: "Print deposit receipt" })).toBeInTheDocument();
  });

  it("a deposit can be taken while stock is short; paying in full can't", async () => {
    const { user } = setup("/invoices/sell", { products: [makeProduct({ ...RING, stock_qty: 0 })] });
    await user.click(await screen.findByRole("button", { name: "Add Heart Ring" }));
    await user.click(screen.getByLabelText("Cash"));
    expect(await screen.findByText("Not enough stock for SKU DL-RING: 0 available, 1 needed.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Take payment" })).toBeDisabled();
    await user.click(screen.getByLabelText("Take a deposit"));
    await user.type(screen.getByLabelText("Deposit amount"), "20");
    expect(await screen.findByRole("button", { name: "Take deposit · $20.00" })).toBeEnabled();
  });

  it("Find an order lists quotes with a balance and links to them", async () => {
    const open = makeInvoice({ quote_number: "QTE-2026-077", customer_name: "Ava Thompson", source: "in_store", amount_paid_cents: 3000 });
    const { user } = setup("/invoices/sell", { invoices: [open] });
    await user.click(await screen.findByText("Find an order to take the balance"));
    await user.type(screen.getByLabelText("Quote number or website order"), "077");
    const link = await screen.findByRole("link", { name: "QTE-2026-077 · Ava Thompson · balance $78.00" });
    expect(link).toHaveAttribute("href", `/invoices/${open.id}`);
  });
});

describe("made to order", () => {
  it("paid in full but not handed over: it stays paid for the bench", async () => {
    const { server, user } = setup("/invoices/sell");
    await user.click(await screen.findByRole("button", { name: "Add Bespoke Band" }));
    await user.click(screen.getByLabelText("Card"));
    await user.click(await screen.findByRole("button", { name: "Take payment · $432.00" }));
    expect(await screen.findByText(/Made to order: INV-2026-\d+ stays paid until it is made and handed over\./)).toBeInTheDocument();
    expect(server.calls.some((c) => c.path.endsWith("/status"))).toBe(false);
  });
});

describe("In store source", () => {
  it("is a filter and a tag on the invoices list", async () => {
    const sale = makeInvoice({ quote_number: "QTE-2026-090", source: "in_store" });
    const { server, user } = setup("/invoices", { invoices: [sale], role: "owner" });
    expect(await screen.findByText("In store", { selector: ".tag--store" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Source"), "In store");
    await waitFor(() => expect(server.last("GET", "/api/v1/invoices")!.query.get("source")).toBe("in_store"));
  });
});
