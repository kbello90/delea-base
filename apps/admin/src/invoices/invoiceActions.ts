import type { AppRole } from "@delea/api-client";
import { STATUSES, type Invoice, type OrderStatus } from "./api";

/** What a role may do to an invoice in its current state. Mirrors the API (which enforces). */
export interface InvoiceActions {
  /** quote -> paid, recording the payment method. */
  markPaid: boolean;
  /** Later statuses this invoice may move to (skips allowed), once it is paid. */
  forward: OrderStatus[];
  editLines: boolean;
  editNotes: boolean;
  editDueDate: boolean;
  correctPaymentMethod: boolean;
  refund: boolean;
  cancel: boolean;
  deleteQuote: boolean;
}

export const NO_ACTIONS: InvoiceActions = {
  markPaid: false,
  forward: [],
  editLines: false,
  editNotes: false,
  editDueDate: false,
  correctPaymentMethod: false,
  refund: false,
  cancel: false,
  deleteQuote: false,
};

// The ledger fields are optional: an invoice without them holds no payments.
type InvoiceState = Pick<Invoice, "order_status" | "paid_at" | "refunded_at" | "cancelled_at"> &
  Partial<Pick<Invoice, "source" | "amount_paid_cents" | "balance_cents" | "production_started_at" | "stock_short" | "payments">>;

export const isClosed = (i: Pick<Invoice, "refunded_at" | "cancelled_at">) =>
  i.refunded_at !== null || i.cancelled_at !== null;

export function invoiceActions(invoice: InvoiceState, role: AppRole | undefined): InvoiceActions {
  if (role !== "owner" && role !== "staff") return NO_ACTIONS; // cpa reads only
  const owner = role === "owner";
  const quote = invoice.order_status === "quote";
  const closed = isClosed(invoice);
  const position = STATUSES.findIndex((s) => s.value === invoice.order_status);
  // A website order keeps the lines and total the shopper agreed to (the API refuses edits);
  // one that won't be paid is cancelled, which keeps its record, rather than deleted.
  const website = invoice.source === "storefront";
  // Slice B1: a quote holding money is a deposit. It can't be cancelled until the deposit is
  // refunded, its lines lock once production starts, and a quote with any payment rows keeps
  // them (cancel it instead of deleting it).
  const held = (invoice.amount_paid_cents ?? 0) > 0;
  const hasPayments = (invoice.payments?.length ?? 0) > 0;
  const card = invoice.payments?.some((p) => p.provider === "stripe" && p.status === "succeeded") ?? false;
  const cardHeld = cardNetCents(invoice) > 0;
  return {
    markPaid: quote && !closed && !invoice.stock_short,
    forward: quote || closed ? [] : STATUSES.slice(position + 1).map((s) => s.value),
    editLines: quote && !closed && !website && !invoice.production_started_at,
    editNotes: true,
    editDueDate: quote,
    correctPaymentMethod: owner && !quote,
    refund: owner && (invoice.paid_at !== null || held) && !closed && !card,
    cancel: owner && !closed && !(quote && held) && !cardHeld,
    deleteQuote: owner && quote && !website && !hasPayments,
  };
}

/** Card money taken online still held (payments minus refunds through Stripe). While it is
 * above zero the invoice can't be cancelled: it is refunded in Stripe first. */
export function cardNetCents(invoice: Partial<Pick<Invoice, "payments">>): number {
  return (invoice.payments ?? [])
    .filter((p) => p.provider === "stripe" && p.status === "succeeded")
    .reduce((sum, p) => sum + (p.kind === "payment" ? p.amount_cents : -p.amount_cents), 0);
}

/** Deposits and payments (Slice B1). Mirrors the API, which enforces. */
export interface PaymentActions {
  /** Record a payment: a deposit or the rest. */
  record: boolean;
  startProduction: boolean;
  /** Complete a quote paid in full that was short of stock. */
  settle: boolean;
  /** Void a payment recorded by mistake (owner, while a quote). */
  voidPayments: boolean;
}

export function paymentActions(invoice: InvoiceState, role: AppRole | undefined): PaymentActions {
  const writer = role === "owner" || role === "staff";
  const open = writer && invoice.order_status === "quote" && !isClosed(invoice);
  const held = (invoice.amount_paid_cents ?? 0) > 0;
  return {
    record: open && (invoice.balance_cents ?? 0) > 0,
    startProduction: open && held && !invoice.production_started_at && !invoice.stock_short,
    settle: open && Boolean(invoice.stock_short),
    voidPayments: open && role === "owner",
  };
}

/** "Deposit paid · In production · $12.00 due" for a quote holding money; null otherwise. */
export function depositLabel(
  invoice: Pick<Invoice, "order_status" | "cancelled_at" | "amount_paid_cents" | "balance_cents" | "production_started_at" | "stock_short">,
  money: (cents: number) => string,
): string | null {
  if (invoice.order_status !== "quote" || invoice.cancelled_at || invoice.amount_paid_cents <= 0) return null;
  if (invoice.stock_short) return "Paid — stock short";
  return ["Deposit paid", invoice.production_started_at ? "In production" : null, `${money(invoice.balance_cents)} due`]
    .filter(Boolean)
    .join(" · ");
}
