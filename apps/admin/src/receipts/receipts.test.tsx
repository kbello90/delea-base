import type { AppRole } from "@delea/api-client";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { depositQuote, invoiceServer, makePayment, makeProduct, paidInvoice, RECEIPT_SETTINGS } from "../invoices/testServer";
import { settingsServer } from "../settings/testServer";
import { fakeBackend, renderApp, tokenFor } from "../test/harness";
import { PAGE_RULES } from "./api";
import { THERMAL_NOTE } from "./PrintReceipt";
import { receiptDateTime } from "./receiptFormat";

// Slice C1: receipts on Letter (the default), 80 mm and 58 mm paper, chosen per receipt.

const RING = makeProduct({ name: "Initial Signet", sku: "DL-SIG", price_cents: 15000 });
const lines = [
  {
    id: "l1",
    product_id: RING.id,
    description: "Initial Signet",
    qty: 2,
    unit_price_cents: 15000,
    line_total_cents: 30000,
    personalization: [
      { label: "Initial", value: "KB" },
      { label: "Size", value: "7" },
    ],
  },
];
const sale = paidInvoice({
  invoice_number: "INV-2026-120",
  source: "in_store",
  subtotal_cents: 30000,
  tax_cents: 2400,
  total_cents: 32400,
  items: lines,
  payments: [makePayment({ amount_cents: 32400, method: "card", received_at: "2026-10-20T15:05:00Z" })],
});

function setup(invoice = sale, { role = "owner", ...data }: { role?: AppRole } & Parameters<typeof invoiceServer>[0] = {}) {
  const server = invoiceServer({ products: [RING], invoices: [invoice], ...data });
  const utils = renderApp({ path: `/invoices/${invoice.id}`, fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { ...utils, server, user: userEvent.setup() };
}

const printRoot = () => document.querySelector<HTMLElement>(".receipt-print-root");
let print: ReturnType<typeof vi.fn>;
beforeEach(() => {
  print = vi.fn();
  window.print = print as unknown as typeof window.print;
});
afterEach(() => {
  act(() => void window.dispatchEvent(new Event("afterprint")));
});

async function openDialog(user: ReturnType<typeof userEvent.setup>, name = "Print receipt") {
  const button = await screen.findByRole("button", { name });
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);
  return within(await screen.findByRole("dialog", { name }));
}

describe("the print dialog", () => {
  it("starts on the shop's default, Letter, with no thermal note", async () => {
    const { user } = setup();
    const dialog = await openDialog(user);
    expect(dialog.getByRole("radio", { name: /Letter \(8\.5 × 11 in\)/ })).toBeChecked();
    expect(dialog.getByText(/your default/)).toHaveTextContent("Any office printer · your default");
    expect(dialog.queryByText(THERMAL_NOTE)).not.toBeInTheDocument();
    const preview = within(dialog.getByRole("region", { name: "Receipt preview" }));
    expect(preview.getByRole("article", { name: "Receipt" })).toHaveAttribute("data-layout", "letter");
    expect(preview.getByRole("columnheader", { name: "Unit price" })).toBeInTheDocument(); // Letter is a table
  });

  it("starts on 80 mm when that is the shop's default", async () => {
    const { user } = setup(sale, { receipt: { ...RECEIPT_SETTINGS, receipt_layout: "80mm" } });
    const dialog = await openDialog(user);
    expect(dialog.getByRole("radio", { name: /80 mm roll/ })).toBeChecked();
    expect(dialog.getByText(THERMAL_NOTE)).toBeInTheDocument();
  });

  it.each([
    ["letter", /Letter/, "@page { size: letter; margin: 0.5in; }", false],
    ["80mm", /80 mm roll/, "@page { size: 80mm auto; margin: 0; }", true],
    ["58mm", /58 mm roll/, "@page { size: 58mm auto; margin: 0; }", true],
  ] as const)("prints on %s: its own @page rule, only the receipt on the page", async (layout, radio, rule, thermal) => {
    const { user } = setup();
    const dialog = await openDialog(user);
    await user.click(dialog.getByRole("radio", { name: radio }));
    expect(Boolean(dialog.queryByText(THERMAL_NOTE))).toBe(thermal); // margins none, scale 100% for rolls only
    expect(within(dialog.getByRole("region", { name: "Receipt preview" })).getByRole("article")).toHaveAttribute("data-layout", layout);
    await user.click(dialog.getByRole("button", { name: "Print" }));

    await waitFor(() => expect(print).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(PAGE_RULES[layout]).toBe(rule);
    expect(printRoot()).toHaveAttribute("data-layout", layout);
    expect(printRoot()!.querySelector("style")!.textContent).toBe(rule);
    expect(printRoot()!.querySelector(".receipt-doc")).toHaveClass(`receipt-doc--${layout}`);
    expect(document.body).toHaveClass("printing-receipt");

    act(() => void window.dispatchEvent(new Event("afterprint")));
    expect(printRoot()).toBeNull();
    expect(document.body).not.toHaveClass("printing-receipt");
  });

  it("a paper picked for one receipt doesn't stick: the next one starts on the default", async () => {
    const { user } = setup();
    let dialog = await openDialog(user);
    await user.click(dialog.getByRole("radio", { name: /58 mm roll/ }));
    await user.click(dialog.getByRole("button", { name: "Cancel" }));
    dialog = await openDialog(user);
    expect(dialog.getByRole("radio", { name: /Letter/ })).toBeChecked();
  });
});

describe("what a receipt says", () => {
  it.each(["letter", "80mm", "58mm"] as const)("a sale on %s: shop, number, lines with answers, totals, payment, footer", async (layout) => {
    const { user } = setup();
    const dialog = await openDialog(user);
    await user.click(dialog.getByRole("radio", { name: new RegExp(layout === "letter" ? "Letter" : layout.replace("mm", " mm")) }));
    const receipt = within(within(dialog.getByRole("region", { name: "Receipt preview" })).getByRole("article", { name: "Receipt" }));
    expect(receipt.getByText("DELEA Jewelry")).toBeInTheDocument();
    expect(receipt.getByText(/12 Main St/)).toHaveTextContent("12 Main St Austin, TX 78701");
    expect(receipt.getByRole("heading", { name: "Receipt" })).toBeInTheDocument();
    expect(receipt.getByText("INV-2026-120")).toBeInTheDocument();
    expect(receipt.getByText(receiptDateTime("2026-10-20T15:05:00Z"))).toBeInTheDocument();
    expect(receipt.getByText(/Initial Signet/)).toBeInTheDocument();
    expect(receipt.getByText("Initial: KB")).toBeInTheDocument();
    expect(receipt.getByText("Size: 7")).toBeInTheDocument();
    expect(receipt.getByText("Tax (8%)").nextSibling).toHaveTextContent("$24.00");
    expect(receipt.getByText("Total").nextSibling).toHaveTextContent("$324.00");
    expect(receipt.getByText("Paid · Card").nextSibling).toHaveTextContent("$324.00");
    expect(receipt.queryByText("Not a sales receipt")).not.toBeInTheDocument();
    expect(receipt.queryByText("Balance due")).not.toBeInTheDocument();
    expect(receipt.getByText(/Thank you!/)).toHaveTextContent("Thank you! Exchanges within 30 days.");
  });

  it("a deposit: a deposit receipt, not a sales receipt, with the balance due", async () => {
    const deposit = depositQuote(3000, { quote_number: "QTE-2026-130", source: "in_store", items: lines.map((l) => ({ ...l, qty: 1, line_total_cents: 15000 })) });
    const { user } = setup(deposit);
    const dialog = await openDialog(user, "Print deposit receipt");
    const receipt = within(dialog.getByRole("article", { name: "Deposit receipt" }));
    expect(receipt.getByRole("heading", { name: "Deposit receipt" })).toBeInTheDocument();
    expect(receipt.getByText("Not a sales receipt")).toBeInTheDocument();
    expect(receipt.getByText("Quote")).toBeInTheDocument();
    expect(receipt.getByText("QTE-2026-130")).toBeInTheDocument();
    expect(receipt.getByText("Deposit · Cash").nextSibling).toHaveTextContent("$30.00");
    expect(receipt.getByText("Balance due").nextSibling).toHaveTextContent("$78.00");
  });

  it("the CPA has no receipt to print; a quote with nothing paid has none either", async () => {
    setup(sale, { role: "cpa" });
    await screen.findByRole("heading", { level: 1, name: "INV-2026-120" });
    expect(screen.queryByRole("button", { name: /Print/ })).not.toBeInTheDocument();
  });
});

describe("receipt settings", () => {
  it("the owner picks the default paper and the footer", async () => {
    const server = settingsServer();
    const user = userEvent.setup();
    renderApp({ path: "/settings", fetch: server.fetch, backend: fakeBackend(tokenFor("owner")) });
    const card = within(await screen.findByRole("form", { name: "Receipts" }));
    expect(card.getByRole("radio", { name: "Letter (8.5 × 11 in)" })).toBeChecked();
    await user.click(card.getByRole("radio", { name: "58 mm roll" }));
    expect(card.getByText(/Margins: None and Scale: 100%/)).toBeInTheDocument();
    await user.type(card.getByLabelText("Receipt footer (optional)"), "Thank you!");
    await user.click(card.getByRole("button", { name: "Save receipt settings" }));
    await waitFor(() =>
      expect(server.last("PATCH", "/api/v1/settings/company")?.body).toEqual({ receipt_layout: "58mm", receipt_footer: "Thank you!" }),
    );
  });
});
