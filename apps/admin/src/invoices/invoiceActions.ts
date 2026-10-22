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

type InvoiceState = Pick<Invoice, "order_status" | "paid_at" | "refunded_at" | "cancelled_at">;

export const isClosed = (i: Pick<Invoice, "refunded_at" | "cancelled_at">) =>
  i.refunded_at !== null || i.cancelled_at !== null;

export function invoiceActions(invoice: InvoiceState, role: AppRole | undefined): InvoiceActions {
  if (role !== "owner" && role !== "staff") return NO_ACTIONS; // cpa reads only
  const owner = role === "owner";
  const quote = invoice.order_status === "quote";
  const closed = isClosed(invoice);
  const position = STATUSES.findIndex((s) => s.value === invoice.order_status);
  return {
    markPaid: quote && !closed,
    forward: quote || closed ? [] : STATUSES.slice(position + 1).map((s) => s.value),
    editLines: quote && !closed,
    editNotes: true,
    editDueDate: quote,
    correctPaymentMethod: owner && !quote,
    refund: owner && invoice.paid_at !== null && !closed,
    cancel: owner && !closed,
    deleteQuote: owner && quote,
  };
}
