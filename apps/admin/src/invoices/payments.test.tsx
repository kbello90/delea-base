import type { AppRole } from "@delea/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeBackend, renderApp, tokenFor } from "../test/harness";
import { depositLabel, invoiceActions, paymentActions } from "./invoiceActions";
import { depositQuote, invoiceServer, makeInvoice, makePayment, makeProduct, paidInvoice } from "./testServer";

// Slice B1: the invoice's payments, deposits, production start and a stock shortfall.

const RING = makeProduct({ name: "Heart Ring", sku: "DL-RING", price_cents: 10000, stock_qty: 5 });
const line = { id: "line-1", product_id: RING.id, description: "Heart Ring", qty: 1, unit_price_cents: 10000, line_total_cents: 10000, personalization: null };

function setup(path: string, { role = "owner", ...data }: { role?: AppRole } & Parameters<typeof invoiceServer>[0] = {}) {
  const server = invoiceServer({ products: [RING], ...data });
  const utils = renderApp({ path, fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { ...utils, server, user: userEvent.setup() };
}

const quote = (overrides = {}) => makeInvoice({ quote_number: "QTE-2026-050", items: [line], ...overrides });
const paidAndDue = () => within(screen.getByLabelText("Paid and due"));

describe("an invoice's payments", () => {
  it("a quote shows its total, nothing paid and the whole balance due", async () => {
    const q = quote();
    setup(`/invoices/${q.id}`, { invoices: [q] });
    expect(await screen.findByRole("heading", { name: "Payments" })).toBeInTheDocument();
    // Amounts appear once the shop's currency is known.
    await waitFor(() => expect(paidAndDue().getByText("Paid so far").nextSibling).toHaveTextContent("$0.00"));
    expect(paidAndDue().getByText("Balance due").nextSibling).toHaveTextContent("$108.00");
    expect(screen.getByText("No payments recorded yet.")).toBeInTheDocument();
  });

  it("records a deposit: amount, method and note go to the API; the quote shows what is due", async () => {
    const q = quote();
    const { server, user } = setup(`/invoices/${q.id}`, { invoices: [q] });
    await user.click(await screen.findByRole("button", { name: "Record payment" }));
    const form = within(screen.getByRole("form", { name: "Record a payment" }));
    const amount = form.getByLabelText("Amount received");
    expect(amount).toHaveValue("108.00"); // starts at the balance
    await user.clear(amount);
    await user.type(amount, "30");
    await user.click(form.getByRole("radio", { name: "Bank transfer" }));
    await user.type(form.getByLabelText("Note (optional)"), "Deposit");
    await user.click(form.getByRole("button", { name: "Record payment" }));

    expect(await screen.findByText("$30.00 recorded. $78.00 still due.")).toBeInTheDocument();
    expect(server.last("POST", `/api/v1/invoices/${q.id}/payments`)!.body).toEqual({ amount_cents: 3000, method: "bank_transfer", note: "Deposit" });
    expect(screen.getAllByText("Deposit paid · $78.00 due")[0]).toHaveClass("tag--deposit");
    const row = within(screen.getByRole("table", { name: `Payments on QTE-2026-050` }));
    expect(row.getByText("Payment · Bank transfer")).toBeInTheDocument();
    expect(row.getByText("$30.00")).toBeInTheDocument();
    expect(paidAndDue().getByText("Balance due").nextSibling).toHaveTextContent("$78.00");
  });

  it("refuses more than the balance, a zero amount and a missing method before asking the API", async () => {
    const q = depositQuote(3000, { quote_number: "QTE-2026-051", items: [line] });
    const { server, user } = setup(`/invoices/${q.id}`, { invoices: [q] });
    await user.click(await screen.findByRole("button", { name: "Record payment" }));
    const form = within(screen.getByRole("form", { name: "Record a payment" }));
    const amount = form.getByLabelText("Amount received");
    await user.clear(amount);
    await user.type(amount, "78.01");
    await user.click(form.getByRole("button", { name: "Record payment" }));
    expect(form.getByText("That is more than the $78.00 still due.")).toBeInTheDocument();
    expect(form.getByText("Choose how it was paid.")).toBeInTheDocument();
    expect(amount).toHaveAttribute("aria-invalid", "true");
    await user.clear(amount);
    await user.type(amount, "0");
    await user.click(form.getByRole("button", { name: "Record payment" }));
    expect(form.getByText("Enter an amount above zero.")).toBeInTheDocument();
    expect(server.all("POST", `/api/v1/invoices/${q.id}/payments`)).toHaveLength(0);
  });

  it("start production, then paying the rest: in production, numbered, nothing due", async () => {
    const q = depositQuote(3000, { quote_number: "QTE-2026-052", items: [line] });
    const { server, user } = setup(`/invoices/${q.id}`, { invoices: [q], role: "staff" });
    await user.click(await screen.findByRole("button", { name: "Start production" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText(/The items lock/)).toBeInTheDocument();
    await user.click(dialog.getByRole("button", { name: "Start production" }));
    expect(await screen.findByText("Production started on QTE-2026-052")).toBeInTheDocument();
    expect(screen.getAllByText("Deposit paid · In production · $78.00 due")[0]).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit lines" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start production" })).not.toBeInTheDocument();
    expect(server.calls.some((c) => c.path.endsWith("/start-production"))).toBe(true);

    await user.click(screen.getByRole("button", { name: "Record payment" }));
    const form = within(screen.getByRole("form", { name: "Record a payment" }));
    expect(form.getByLabelText("Amount received")).toHaveValue("78.00");
    await user.click(form.getByRole("radio", { name: "Card" }));
    await user.click(form.getByRole("button", { name: "Record payment" }));
    expect(await screen.findByText(/^INV-2026-\d+ is paid in full$/)).toBeInTheDocument();
    expect(screen.getAllByText("In production")[0]).toHaveClass("tag--ok");
    expect(paidAndDue().getByText("Balance due").nextSibling).toHaveTextContent("Paid in full");
    expect(screen.queryByRole("button", { name: "Record payment" })).not.toBeInTheDocument();
  });

  it("paid in full but short of stock: a banner, then Complete order once restocked", async () => {
    const short = makeProduct({ name: "Last Ring", sku: "DL-LAST", stock_qty: 0 });
    const q = makeInvoice({
      quote_number: "QTE-2026-053",
      items: [{ ...line, product_id: short.id, description: "Last Ring" }],
      amount_paid_cents: 10800,
      balance_cents: 0,
      stock_short: true,
      payments: [makePayment({ amount_cents: 10800 })],
    });
    const { server, user } = setup(`/invoices/${q.id}`, { invoices: [q], products: [short] });
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("Paid in full, but there isn't enough stock to complete this order.");
    expect((await screen.findAllByText("Paid — stock short"))[0]).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark paid" })).not.toBeInTheDocument();

    await user.click(within(banner).getByRole("button", { name: "Complete order" }));
    expect(await within(banner).findByText("Not enough stock for SKU DL-LAST: 0 available, 1 needed.")).toBeInTheDocument();
    server.products.get(short.id)!.stock_qty = 1;
    await user.click(within(banner).getByRole("button", { name: "Complete order" }));
    expect(await screen.findByText(/^INV-2026-\d+ is paid$/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/isn't enough stock/)).not.toBeInTheDocument());
  });

  it("the owner voids a mistaken payment; it stays listed, crossed out, and counts for nothing", async () => {
    const q = depositQuote(3000, { quote_number: "QTE-2026-054", items: [line] });
    const { server, user } = setup(`/invoices/${q.id}`, { invoices: [q] });
    await user.click(await screen.findByRole("button", { name: /^Void the \$30\.00 payment/ }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Void payment" }));
    expect(await screen.findByText("$30.00 voided")).toBeInTheDocument();
    expect(server.calls.some((c) => c.path === `/api/v1/invoices/${q.id}/payments/${q.payments![0].id}/void`)).toBe(true);
    const row = screen.getByText("Voided").closest("tr")!;
    expect(row).toHaveClass("payment-row--void");
    expect(paidAndDue().getByText("Paid so far").nextSibling).toHaveTextContent("$0.00");
  });

  it("a quote holding a deposit can't be cancelled or deleted; the deposit is refunded first", async () => {
    const q = depositQuote(2000, { quote_number: "QTE-2026-055", items: [line] });
    const { user } = setup(`/invoices/${q.id}`, { invoices: [q] });
    await screen.findByRole("heading", { name: "Payments" });
    expect(screen.queryByRole("button", { name: "Cancel invoice" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete quote" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Refund deposit" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByRole("heading", { name: "Refund the $20.00 deposit on QTE-2026-055?" })).toBeInTheDocument();
    await user.click(dialog.getByRole("button", { name: "Refund deposit" }));
    expect(await screen.findByRole("button", { name: "Cancel invoice" })).toBeInTheDocument();
    expect(screen.getByText("−$20.00")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete quote" })).not.toBeInTheDocument(); // it has payment rows
  });

  it("staff record payments but don't void them; the CPA reads the ledger only", async () => {
    const q = depositQuote(3000, { quote_number: "QTE-2026-056", items: [line] });
    setup(`/invoices/${q.id}`, { invoices: [q], role: "staff" });
    expect(await screen.findByRole("button", { name: "Record payment" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Void/ })).not.toBeInTheDocument();
  });

  it("the CPA sees the payments with no buttons", async () => {
    const q = depositQuote(3000, { quote_number: "QTE-2026-057", items: null });
    setup(`/invoices/${q.id}`, { invoices: [q], role: "cpa" });
    const table = within(await screen.findByRole("table", { name: "Payments on QTE-2026-057" }));
    expect(await table.findByText("$30.00")).toBeInTheDocument();
    expect(table.getByText("Deposit")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record payment" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Void/ })).not.toBeInTheDocument();
  });

  it("an invoice paid by card online is refunded in Stripe: no Refund button", async () => {
    const card = paidInvoice({ quote_number: "QTE-2026-059", items: [line], payments: [makePayment({ provider: "stripe", method: "card", amount_cents: 10800 })] });
    setup(`/invoices/${card.id}`, { invoices: [card] });
    await screen.findByRole("heading", { name: "Payments" });
    expect(screen.queryByRole("button", { name: "Refund" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Void/ })).not.toBeInTheDocument();
  });

  it("the list shows a deposit's state and what is due", async () => {
    const q = depositQuote(3000, { quote_number: "QTE-2026-058", production_started_at: "2026-10-22T15:45:00Z" });
    setup("/invoices", { invoices: [q] });
    const row = within((await screen.findByRole("link", { name: "QTE-2026-058" })).closest("tr")!);
    expect(row.getByText("Deposit paid · In production · $78.00 due")).toBeInTheDocument();
    expect(row.queryByText("Quote")).not.toBeInTheDocument();
  });
});

describe("paymentActions and depositLabel", () => {
  const usd = (c: number) => `$${(c / 100).toFixed(2)}`;
  const held = { ...depositQuote(3000), production_started_at: null };

  it("record while something is due; start production once, on a deposit; void for the owner", () => {
    expect(paymentActions(quote(), "staff")).toEqual({ record: true, startProduction: false, settle: false, voidPayments: false });
    expect(paymentActions(held, "owner")).toEqual({ record: true, startProduction: true, settle: false, voidPayments: true });
    expect(paymentActions({ ...held, production_started_at: "2026-10-22T00:00:00Z" }, "staff").startProduction).toBe(false);
    expect(paymentActions(held, "cpa")).toEqual({ record: false, startProduction: false, settle: false, voidPayments: false });
    expect(paymentActions(paidInvoice(), "owner").record).toBe(false);
    expect(paymentActions({ ...held, cancelled_at: "2026-10-22T00:00:00Z" }, "owner").record).toBe(false);
  });

  it("a deposit locks cancel, delete and, once in production, the lines", () => {
    const actions = invoiceActions({ ...held, production_started_at: "2026-10-22T00:00:00Z" }, "owner");
    expect([actions.cancel, actions.deleteQuote, actions.editLines, actions.refund, actions.markPaid]).toEqual([false, false, false, true, true]);
    expect(invoiceActions({ ...held, stock_short: true, balance_cents: 0 }, "owner").markPaid).toBe(false);
  });

  it("labels", () => {
    expect(depositLabel(held, usd)).toBe("Deposit paid · $78.00 due");
    expect(depositLabel({ ...held, production_started_at: "2026-10-22T00:00:00Z" }, usd)).toBe("Deposit paid · In production · $78.00 due");
    expect(depositLabel({ ...held, stock_short: true }, usd)).toBe("Paid — stock short");
    expect(depositLabel(quote(), usd)).toBeNull();
    expect(depositLabel(paidInvoice(), usd)).toBeNull();
  });
});
