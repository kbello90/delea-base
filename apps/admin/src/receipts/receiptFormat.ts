import type { Invoice } from "../invoices/api";

/** Holding money but not paid in full: a deposit receipt, never a sales receipt. */
export const isDepositReceipt = (invoice: Invoice) => invoice.order_status === "quote" && invoice.amount_paid_cents > 0;

/** When the money was taken: paid in full, else the latest payment (a deposit), else created. */
export function receiptTime(invoice: Invoice): string {
  if (invoice.paid_at) return invoice.paid_at;
  const received = (invoice.payments ?? []).filter((p) => p.kind === "payment" && p.status === "succeeded").map((p) => p.received_at);
  return received.sort().at(-1) ?? invoice.created_at;
}

/**
 * The date and time on the receipt, with the time zone named. Until the shop's own time zone
 * is a setting (Slice C3) this is the till's (the browser's) time zone.
 */
export function receiptDateTime(iso: string, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-US", {
    // Named parts: dateStyle/timeStyle can't be combined with timeZoneName.
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
    timeZoneName: "short",
  }).format(new Date(iso));
}
