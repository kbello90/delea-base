import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorState } from "../components/ErrorState";
import { Field } from "../components/Field";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { useToast } from "../components/toastContext";
import { formatMoney } from "../lib/money";
import { centsToInput, parseMoneyInput } from "../lib/moneyInput";
import { useCurrency } from "../lib/useCurrency";
import {
  documentNumber,
  invoiceMethodLabel,
  methodLabel,
  PAYMENT_METHODS,
  statusLabel,
  useCancel,
  useChangeStatus,
  useDeleteInvoice,
  useInvoice,
  useRecordPayment,
  useRefund,
  useSettle,
  useStartProduction,
  useUpdateInvoice,
  useVoidPayment,
  type Invoice,
  type OrderStatus,
  type Payment,
  type PaymentMethod,
} from "./api";
import { Totals } from "./Cart";
import { formatDateTime, formatDay } from "./format";
import { invoiceActions, paymentActions, type InvoiceActions, type PaymentActions } from "./invoiceActions";
import { StatusTags } from "./InvoicesPage";
import "./invoices.css";

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Please try again.");

/** Payment method radios (shared by Mark paid and the sale screen). */
export function PaymentMethodChoice({
  name,
  value,
  onChange,
  disabled = false,
}: {
  name: string;
  value: PaymentMethod | null;
  onChange: (m: PaymentMethod) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="method-choice">
      <legend>Payment method</legend>
      <div className="choice-row">
        {PAYMENT_METHODS.map((m) => (
          <label key={m.value} className="check method-choice__option">
            <input
              type="radio"
              name={name}
              value={m.value}
              checked={value === m.value}
              onChange={() => onChange(m.value)}
              disabled={disabled}
            />
            {m.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

type Pending =
  | { kind: "pay" }
  | { kind: "status"; status: OrderStatus }
  | { kind: "refund" }
  | { kind: "cancel" }
  | { kind: "delete" };

function StatusActions({ invoice, actions, money }: { invoice: Invoice; actions: InvoiceActions; money: (cents: number) => string }) {
  const number = documentNumber(invoice);
  const deposit = invoice.order_status === "quote" && invoice.amount_paid_cents > 0;
  const toast = useToast();
  const navigate = useNavigate();
  const [pending, setPending] = useState<Pending | null>(null);
  const [method, setMethod] = useState<PaymentMethod | null>(null);
  const change = useChangeStatus();
  const refund = useRefund(invoice.id);
  const cancel = useCancel(invoice.id);
  const remove = useDeleteInvoice();
  const busy = change.isPending || refund.isPending || cancel.isPending || remove.isPending;
  const error = change.error ?? refund.error ?? cancel.error ?? remove.error;

  // A website order's payment is what the shopper chose (pay on pickup: cash by default).
  const website = invoice.source === "storefront";
  function open(next: Pending) {
    change.reset();
    refund.reset();
    cancel.reset();
    remove.reset();
    setMethod(website && invoice.payment_choice ? CHOICE_METHOD[invoice.payment_choice] : null);
    setPending(next);
  }

  function confirm() {
    if (!pending) return;
    const close = (title: string) => {
      setPending(null);
      toast({ title, variant: "success" });
    };
    if (pending.kind === "pay") {
      if (!method) return;
      change.mutate(
        { id: invoice.id, status: "paid", paymentMethod: method },
        { onSuccess: (paid) => close(`${documentNumber(paid)} is paid`) },
      );
    } else if (pending.kind === "status") {
      change.mutate(
        { id: invoice.id, status: pending.status },
        { onSuccess: () => close(`${number} is ${statusLabel(pending.status).toLowerCase()}`) },
      );
    } else if (pending.kind === "refund") {
      refund.mutate(undefined, { onSuccess: () => close(`${number} is refunded`) });
    } else if (pending.kind === "cancel") {
      cancel.mutate(undefined, { onSuccess: () => close(`${number} is cancelled`) });
    } else {
      remove.mutate(invoice.id, {
        onSuccess: () => {
          toast({ title: `${number} was deleted`, variant: "success" });
          navigate("/invoices");
        },
      });
    }
  }

  const any =
    actions.markPaid || actions.forward.length > 0 || actions.refund || actions.cancel || actions.deleteQuote;
  if (!any) return null;

  const dialog = (() => {
    if (!pending) return null;
    switch (pending.kind) {
      case "pay":
        return {
          title: website ? `Confirm the payment for ${invoice.order_ref ?? number}?` : `Mark ${number} paid?`,
          description: (
            <>
              <p>
                {website ? "Do this once the money has arrived. " : ""}
                {deposit ? `This records the remaining ${money(invoice.balance_cents)} as paid. ` : ""}
                This gives it an invoice number and takes its items out of stock. It can't be undone.
              </p>
              <PaymentMethodChoice name="mark-paid-method" value={method} onChange={setMethod} disabled={busy} />
            </>
          ),
          confirmLabel: website ? "Confirm payment" : "Mark paid",
        };
      case "status":
        return {
          title: `Mark ${number} ${statusLabel(pending.status).toLowerCase()}?`,
          description: <p>The status only moves forward, so this can't be undone.</p>,
          confirmLabel: `Mark ${statusLabel(pending.status).toLowerCase()}`,
        };
      case "refund":
        return deposit
          ? {
              title: `Refund the ${money(invoice.amount_paid_cents)} deposit on ${number}?`,
              description: <p>Record this once the money is back with the customer. The quote stays open with nothing held, so it can then be cancelled.</p>,
              confirmLabel: "Refund deposit",
              danger: true,
            }
          : {
              title: `Refund ${number}?`,
              description: (
                <p>
                  {invoice.amount_paid_cents > 0 ? `This records a refund of ${money(invoice.amount_paid_cents)}. ` : ""}It's marked refunded and its status stops
                  changing. Stock isn't put back.
                </p>
              ),
              confirmLabel: "Refund",
              danger: true,
            };
      case "cancel":
        return {
          title: `Cancel ${number}?`,
          description: <p>It's marked cancelled and its status stops changing. Stock isn't put back.</p>,
          confirmLabel: "Cancel invoice",
          danger: true,
        };
      default:
        return {
          title: `Delete ${number}?`,
          description: <p>The quote and its items are removed. This can't be undone.</p>,
          confirmLabel: "Delete quote",
          danger: true,
        };
    }
  })();

  return (
    <section className="invoice-section" aria-labelledby="actions-heading">
      <h2 id="actions-heading">Actions</h2>
      <div className="invoice-actions">
        {actions.markPaid && (
          <Button type="button" onClick={() => open({ kind: "pay" })}>
            {website ? "Confirm payment" : "Mark paid"}
          </Button>
        )}
        {actions.forward.map((status) => (
          <Button key={status} type="button" variant="ghost" onClick={() => open({ kind: "status", status })}>
            Mark {statusLabel(status).toLowerCase()}
          </Button>
        ))}
        {actions.refund && (
          <button type="button" className="btn-text btn-text--danger" onClick={() => open({ kind: "refund" })}>
            {deposit ? "Refund deposit" : "Refund"}
          </button>
        )}
        {actions.cancel && (
          <button type="button" className="btn-text btn-text--danger" onClick={() => open({ kind: "cancel" })}>
            Cancel invoice
          </button>
        )}
        {actions.deleteQuote && (
          <button type="button" className="btn-text btn-text--danger" onClick={() => open({ kind: "delete" })}>
            Delete quote
          </button>
        )}
      </div>
      {dialog && (
        <ConfirmDialog
          open
          onOpenChange={(next) => !next && setPending(null)}
          title={dialog.title}
          description={dialog.description}
          confirmLabel={dialog.confirmLabel}
          danger={dialog.danger}
          busy={busy}
          confirmDisabled={pending?.kind === "pay" && !method}
          error={error ? message(error) : null}
          onConfirm={confirm}
        />
      )}
    </section>
  );
}

function NotesEditor({ invoice }: { invoice: Invoice }) {
  const [editing, setEditing] = useState(false);
  const [notes, setNotes] = useState(invoice.notes ?? "");
  const update = useUpdateInvoice(invoice.id);
  const toast = useToast();

  function submit(event: FormEvent) {
    event.preventDefault();
    update.mutate(
      { notes: notes.trim() ? notes : null },
      {
        onSuccess: () => {
          setEditing(false);
          toast({ title: "Notes saved", variant: "success" });
        },
      },
    );
  }

  if (!editing) {
    return (
      <div className="invoice-notes">
        <p>{invoice.notes ? invoice.notes : <span className="muted">No notes.</span>}</p>
        <button
          type="button"
          className="btn-text"
          onClick={() => {
            setNotes(invoice.notes ?? "");
            update.reset();
            setEditing(true);
          }}
        >
          Edit notes
        </button>
      </div>
    );
  }
  return (
    <form className="record-form" onSubmit={submit}>
      {update.isError && (
        <p className="form-error" role="alert">
          {message(update.error)}
        </p>
      )}
      <Field id="invoice-notes" label="Notes">
        <textarea id="invoice-notes" rows={3} maxLength={5000} value={notes} onChange={(e) => setNotes(e.target.value)} disabled={update.isPending} />
      </Field>
      <div className="form-actions">
        <Button type="submit" disabled={update.isPending}>
          {update.isPending ? "Saving…" : "Save notes"}
        </Button>
        <button type="button" className="btn-text" onClick={() => setEditing(false)} disabled={update.isPending}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function DueDateEditor({ invoice }: { invoice: Invoice }) {
  const [editing, setEditing] = useState(false);
  const [day, setDay] = useState(invoice.due_date ?? "");
  const update = useUpdateInvoice(invoice.id);
  if (!editing) {
    return (
      <>
        {invoice.due_date ? formatDay(invoice.due_date) : <span className="muted">On receipt</span>}{" "}
        <button type="button" className="btn-text" onClick={() => setEditing(true)} aria-label="Change due date">
          Change
        </button>
      </>
    );
  }
  return (
    <form
      className="inline-edit"
      onSubmit={(e) => {
        e.preventDefault();
        update.mutate({ due_date: day || null }, { onSuccess: () => setEditing(false) });
      }}
    >
      <label htmlFor="due-date" className="visually-hidden">
        Due date
      </label>
      <input id="due-date" type="date" value={day} onChange={(e) => setDay(e.target.value)} disabled={update.isPending} />
      <button type="submit" className="btn-text" disabled={update.isPending}>
        Save
      </button>
      <button type="button" className="btn-text" onClick={() => setEditing(false)} disabled={update.isPending}>
        Cancel
      </button>
      {update.isError && (
        <span className="field-error" role="alert">
          {message(update.error)}
        </span>
      )}
    </form>
  );
}

function PaymentMethodEditor({ invoice }: { invoice: Invoice }) {
  const [editing, setEditing] = useState(false);
  const [method, setMethod] = useState<PaymentMethod | "">(invoice.payment_method ?? "");
  const update = useUpdateInvoice(invoice.id);
  const shown = invoiceMethodLabel(invoice) ?? <span className="muted">Not recorded</span>;
  if (!editing) {
    return (
      <>
        {shown}{" "}
        <button type="button" className="btn-text" onClick={() => setEditing(true)} aria-label="Change payment method">
          Change
        </button>
      </>
    );
  }
  return (
    <form
      className="inline-edit"
      onSubmit={(e) => {
        e.preventDefault();
        if (method) update.mutate({ payment_method: method }, { onSuccess: () => setEditing(false) });
      }}
    >
      <label htmlFor="payment-method" className="visually-hidden">
        Payment method
      </label>
      <select id="payment-method" value={method} onChange={(e) => setMethod(e.target.value as PaymentMethod)} disabled={update.isPending}>
        <option value="" disabled>
          Choose…
        </option>
        {PAYMENT_METHODS.map((m) => (
          <option key={m.value} value={m.value}>
            {m.label}
          </option>
        ))}
      </select>
      <button type="submit" className="btn-text" disabled={update.isPending || !method}>
        Save
      </button>
      <button type="button" className="btn-text" onClick={() => setEditing(false)} disabled={update.isPending}>
        Cancel
      </button>
      {update.isError && (
        <span className="field-error" role="alert">
          {message(update.error)}
        </span>
      )}
    </form>
  );
}

const KIND_LABEL = { payment: "Payment", refund: "Refund" } as const;

/** Record a deposit or the rest. The amount starts at the balance; the API refuses more. */
function RecordPaymentForm({ invoice, money, onClose }: { invoice: Invoice; money: (cents: number) => string; onClose: () => void }) {
  const website = invoice.source === "storefront";
  const [amount, setAmount] = useState(centsToInput(invoice.balance_cents));
  const [method, setMethod] = useState<PaymentMethod | null>(website && invoice.payment_choice ? CHOICE_METHOD[invoice.payment_choice] : null);
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<{ amount?: string; method?: string }>({});
  const record = useRecordPayment(invoice.id);
  const toast = useToast();

  function submit(event: FormEvent) {
    event.preventDefault();
    const parsed = parseMoneyInput(amount);
    const next: { amount?: string; method?: string } = {};
    if (!parsed.ok) next.amount = parsed.error;
    else if (parsed.cents === 0) next.amount = "Enter an amount above zero.";
    else if (parsed.cents > invoice.balance_cents) next.amount = `That is more than the ${money(invoice.balance_cents)} still due.`;
    if (!method) next.method = "Choose how it was paid.";
    setErrors(next);
    if (!parsed.ok || next.amount || !method) return;
    record.mutate(
      { amount_cents: parsed.cents, method, note: note.trim() || null },
      {
        onSuccess: (updated) => {
          onClose();
          toast({
            title:
              updated.order_status !== "quote"
                ? `${documentNumber(updated)} is paid in full`
                : updated.stock_short
                  ? "Payment recorded. There isn't enough stock to complete the order."
                  : `${money(parsed.cents)} recorded. ${money(updated.balance_cents)} still due.`,
            variant: "success",
          });
        },
      },
    );
  }

  return (
    <form className="record-form record-payment" onSubmit={submit} aria-labelledby="record-payment-heading" noValidate>
      <h3 id="record-payment-heading">Record a payment</h3>
      {record.isError && (
        <p className="form-error" role="alert">
          {message(record.error)}
        </p>
      )}
      <Field id="payment-amount" label="Amount received" error={errors.amount} hint={`Up to ${money(invoice.balance_cents)}. Less is a deposit.`}>
        <input
          id="payment-amount"
          type="text"
          inputMode="decimal"
          className="amount-input"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          aria-invalid={errors.amount ? true : undefined}
          aria-describedby={errors.amount ? "payment-amount-error" : "payment-amount-hint"}
          disabled={record.isPending}
        />
      </Field>
      <PaymentMethodChoice name="record-payment-method" value={method} onChange={setMethod} disabled={record.isPending} />
      {errors.method && (
        <p className="field-error" role="alert">
          {errors.method}
        </p>
      )}
      <Field id="payment-note" label="Note (optional)" hint="For example: deposit, paid at the market.">
        <input id="payment-note" type="text" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} disabled={record.isPending} />
      </Field>
      <div className="form-actions">
        <Button type="submit" disabled={record.isPending}>
          {record.isPending ? "Recording…" : "Record payment"}
        </Button>
        <button type="button" className="btn-text" onClick={onClose} disabled={record.isPending}>
          Cancel
        </button>
      </div>
    </form>
  );
}

type PaymentPending = { kind: "void"; payment: Payment } | { kind: "start" };

/** What has been paid, what is due, the ledger, and the deposit actions (Slice B1). */
function PaymentsSection({ invoice, actions, money }: { invoice: Invoice; actions: PaymentActions; money: (cents: number) => string }) {
  const number = documentNumber(invoice);
  const payments = invoice.payments ?? [];
  const [recording, setRecording] = useState(false);
  const [pending, setPending] = useState<PaymentPending | null>(null);
  const voidPayment = useVoidPayment(invoice.id);
  const start = useStartProduction(invoice.id);
  const toast = useToast();
  const busy = voidPayment.isPending || start.isPending;
  const error = voidPayment.error ?? start.error;
  const closed = invoice.cancelled_at !== null || invoice.refunded_at !== null;

  function confirm() {
    if (!pending) return;
    const done = (title: string) => {
      setPending(null);
      toast({ title, variant: "success" });
    };
    if (pending.kind === "void") {
      voidPayment.mutate(pending.payment.id, { onSuccess: () => done(`${money(pending.payment.amount_cents)} voided`) });
    } else {
      start.mutate(undefined, { onSuccess: () => done(`Production started on ${number}`) });
    }
  }

  return (
    <section className="invoice-section" aria-labelledby="payments-heading">
      <h2 id="payments-heading">Payments</h2>
      <dl className="balance-summary" aria-label="Paid and due">
        <div>
          <dt>Total</dt>
          <dd>{money(invoice.total_cents)}</dd>
        </div>
        <div>
          <dt>Paid so far</dt>
          <dd>{money(invoice.amount_paid_cents)}</dd>
        </div>
        <div>
          <dt>{invoice.balance_cents < 0 ? "Overpaid" : "Balance due"}</dt>
          <dd>{invoice.balance_cents === 0 && !closed ? "Paid in full" : money(Math.abs(invoice.balance_cents))}</dd>
        </div>
        {invoice.production_started_at && (
          <div>
            <dt>Production started</dt>
            <dd className="balance-summary__date">{formatDateTime(invoice.production_started_at)}</dd>
          </div>
        )}
      </dl>

      {payments.length === 0 ? (
        <p className="muted">No payments recorded yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-grid">
            <caption className="visually-hidden">Payments on {number}</caption>
            <thead>
              <tr>
                <th scope="col">Received</th>
                <th scope="col">Payment</th>
                <th scope="col" className="num">
                  Amount
                </th>
                <th scope="col">Note</th>
                {actions.voidPayments && (
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {payments.map((p) => {
                const voided = p.status === "voided";
                return (
                  <tr key={p.id} className={voided ? "payment-row--void" : undefined}>
                    <td data-label="Received">{formatDateTime(p.received_at)}</td>
                    <td data-label="Payment">
                      {KIND_LABEL[p.kind]} · {methodLabel(p.method)} {voided && <span className="tag">Voided</span>}
                      {p.status === "pending" && <span className="tag">Pending</span>}
                    </td>
                    <td data-label="Amount" className="num">
                      {p.kind === "refund" ? `−${money(p.amount_cents)}` : money(p.amount_cents)}
                    </td>
                    <td data-label="Note">{p.note ?? <span className="muted">—</span>}</td>
                    {actions.voidPayments && (
                      <td>
                        {p.status === "succeeded" && p.provider === "manual" && (
                          <button
                            type="button"
                            className="btn-text btn-text--danger"
                            onClick={() => {
                              voidPayment.reset();
                              setPending({ kind: "void", payment: p });
                            }}
                            aria-label={`Void the ${money(p.amount_cents)} ${KIND_LABEL[p.kind].toLowerCase()} of ${formatDateTime(p.received_at)}`}
                          >
                            Void
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {(actions.record || actions.startProduction) && !recording && (
        <div className="invoice-actions payments-actions">
          {actions.record && (
            <Button type="button" onClick={() => setRecording(true)}>
              Record payment
            </Button>
          )}
          {actions.startProduction && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                start.reset();
                setPending({ kind: "start" });
              }}
            >
              Start production
            </Button>
          )}
        </div>
      )}
      {recording && <RecordPaymentForm invoice={invoice} money={money} onClose={() => setRecording(false)} />}

      {pending && (
        <ConfirmDialog
          open
          onOpenChange={(next) => !next && setPending(null)}
          title={pending.kind === "void" ? `Void this ${money(pending.payment.amount_cents)} ${KIND_LABEL[pending.payment.kind].toLowerCase()}?` : `Start production on ${number}?`}
          description={
            pending.kind === "void" ? (
              <p>Only for a payment recorded by mistake. It stays on the list, crossed out, and counts for nothing. If the money was really received and given back, refund it instead.</p>
            ) : (
              <p>
                Do this when work begins on the deposit. The items lock, and paying the rest makes it in production rather than paid. It can&apos;t be undone.
              </p>
            )
          }
          confirmLabel={pending.kind === "void" ? "Void payment" : "Start production"}
          danger={pending.kind === "void"}
          busy={busy}
          error={error ? message(error) : null}
          onConfirm={confirm}
        />
      )}
    </section>
  );
}

/** Paid in full, but the stock wasn't there to complete it. */
function StockShortNotice({ invoice, canSettle }: { invoice: Invoice; canSettle: boolean }) {
  const settle = useSettle(invoice.id);
  const toast = useToast();
  return (
    <div className="notice" role="alert">
      <p>
        <strong>Paid in full, but there isn&apos;t enough stock to complete this order.</strong> The payment is kept. Restock the items, then complete it.
      </p>
      {settle.isError && <p className="field-error">{message(settle.error)}</p>}
      {canSettle && (
        <Button
          type="button"
          disabled={settle.isPending}
          onClick={() => settle.mutate(undefined, { onSuccess: (done) => toast({ title: `${documentNumber(done)} is paid`, variant: "success" }) })}
        >
          {settle.isPending ? "Completing…" : "Complete order"}
        </Button>
      )}
    </div>
  );
}

const CHOICE_LABEL = { bank_transfer: "Bank transfer", pay_on_pickup: "Pay on pickup" } as const;
const CHOICE_METHOD = { bank_transfer: "bank_transfer", pay_on_pickup: "cash" } as const;

/** The shopper and where it goes (owner and staff only; the API never sends it to the CPA). */
function ShopperCard({ invoice }: { invoice: Invoice }) {
  const c = invoice.contact;
  return (
    <section className="invoice-section" aria-labelledby="shopper-heading">
      <h2 id="shopper-heading">Shopper</h2>
      {!c ? (
        <p className="muted">The shopper's details aren't visible to your role.</p>
      ) : (
        <dl className="facts">
          <div>
            <dt>Name</dt>
            <dd>
              {c.first_name} {c.last_name}
            </dd>
          </div>
          <div>
            <dt>Email</dt>
            <dd>
              <a href={`mailto:${c.email}`}>{c.email}</a>
            </dd>
          </div>
          {c.phone && (
            <div>
              <dt>Phone</dt>
              <dd>
                <a href={`tel:${c.phone}`}>{c.phone}</a>
              </dd>
            </div>
          )}
          <div>
            <dt>{invoice.fulfillment === "pickup" ? "Pickup" : "Ship to"}</dt>
            <dd>
              {invoice.fulfillment === "pickup" ? (
                "Picks up at the studio"
              ) : (
                <address>
                  {c.ship_line1}
                  <br />
                  {c.ship_line2 && (
                    <>
                      {c.ship_line2}
                      <br />
                    </>
                  )}
                  {c.ship_city}, {c.ship_state} {c.ship_postal_code}
                </address>
              )}
            </dd>
          </div>
          <div>
            <dt>Paying by</dt>
            <dd>{invoice.payment_choice ? CHOICE_LABEL[invoice.payment_choice] : "—"}</dd>
          </div>
          {c.note && (
            <div>
              <dt>Note from the shopper</dt>
              <dd>{c.note}</dd>
            </div>
          )}
        </dl>
      )}
    </section>
  );
}

function InvoiceView({
  invoice,
  actions,
  payments,
  canLinkCustomer,
}: {
  invoice: Invoice;
  actions: InvoiceActions;
  payments: PaymentActions;
  canLinkCustomer: boolean;
}) {
  const currency = useCurrency();
  const money = (cents: number) => (currency.status === "success" ? formatMoney(cents, currency.currency) : "…");
  const number = documentNumber(invoice);
  const closedNote = invoice.cancelled_at
    ? "This invoice is cancelled, so its status can't change."
    : invoice.refunded_at
      ? "This invoice is refunded, so its status can't change."
      : null;

  return (
    <>
      <PageHeader
        title={number}
        description={<StatusTags invoice={invoice} />}
        actions={
          actions.editLines ? (
            <Link to={`/invoices/${invoice.id}/edit`} className="dl-button">
              Edit lines
            </Link>
          ) : undefined
        }
      />
      <Link to="/invoices" className="btn-text back-link">
        Back to invoices
      </Link>
      {closedNote && (
        <p className="notice" role="status">
          {closedNote}
        </p>
      )}
      {invoice.stock_short && !invoice.cancelled_at && <StockShortNotice invoice={invoice} canSettle={payments.settle} />}

      <section className="invoice-section" aria-labelledby="details-heading">
        <h2 id="details-heading" className="visually-hidden">
          Details
        </h2>
        <dl className="facts">
          <div>
            <dt>Customer</dt>
            <dd>
              {invoice.customer_id === null ? (
                <span className="tag">Walk-in</span>
              ) : canLinkCustomer ? (
                <Link to={`/customers/${invoice.customer_id}`}>{invoice.customer_name}</Link>
              ) : (
                invoice.customer_name
              )}
            </dd>
          </div>
          {invoice.source === "storefront" && (
            <div>
              <dt>Website order</dt>
              <dd>
                <span className="tag tag--web">Website</span> {invoice.order_ref}
              </dd>
            </div>
          )}
          <div>
            <dt>Quote number</dt>
            <dd>{invoice.quote_number ?? "—"}</dd>
          </div>
          <div>
            <dt>Invoice number</dt>
            <dd>{invoice.invoice_number ?? <span className="muted">Assigned when paid</span>}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{formatDateTime(invoice.created_at)}</dd>
          </div>
          {invoice.paid_at && (
            <div>
              <dt>Paid</dt>
              <dd>{formatDateTime(invoice.paid_at)}</dd>
            </div>
          )}
          {invoice.order_status !== "quote" && (
            <div>
              <dt>Payment method</dt>
              <dd>
                {actions.correctPaymentMethod ? (
                  <PaymentMethodEditor invoice={invoice} />
                ) : (
                  (invoiceMethodLabel(invoice) ?? <span className="muted">Not recorded</span>)
                )}
              </dd>
            </div>
          )}
          <div>
            <dt>Due</dt>
            <dd>
              {actions.editDueDate ? (
                <DueDateEditor invoice={invoice} />
              ) : invoice.due_date ? (
                formatDay(invoice.due_date)
              ) : (
                <span className="muted">On receipt</span>
              )}
            </dd>
          </div>
          {invoice.refunded_at && (
            <div>
              <dt>Refunded</dt>
              <dd>{formatDateTime(invoice.refunded_at)}</dd>
            </div>
          )}
          {invoice.cancelled_at && (
            <div>
              <dt>Cancelled</dt>
              <dd>{formatDateTime(invoice.cancelled_at)}</dd>
            </div>
          )}
        </dl>
      </section>

      <section className="invoice-section" aria-labelledby="items-heading">
        <h2 id="items-heading">Items</h2>
        {invoice.items === null || invoice.items === undefined ? (
          <p className="muted">Line items aren't visible to your role.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-grid">
              <caption className="visually-hidden">Items on {number}</caption>
              <thead>
                <tr>
                  <th scope="col">Item</th>
                  <th scope="col">Qty</th>
                  <th scope="col" className="num">
                    Unit price
                  </th>
                  <th scope="col" className="num">
                    Line total
                  </th>
                </tr>
              </thead>
              <tbody>
                {invoice.items.map((line) => (
                  <tr key={line.id}>
                    <td className="cell-title">
                      {line.description}
                      {line.personalization && line.personalization.length > 0 && (
                        <ul className="line-answers" aria-label={`Personalization for ${line.description}`}>
                          {line.personalization.map((answer) => (
                            <li key={answer.label}>
                              <span className="line-answers__label">{answer.label}:</span> {answer.value}
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td data-label="Qty">{line.qty}</td>
                    <td data-label="Unit price" className="num">
                      {money(line.unit_price_cents)}
                    </td>
                    <td data-label="Line total" className="num">
                      {money(line.line_total_cents)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Totals totals={invoice} pending={false} />
      </section>

      <PaymentsSection invoice={invoice} actions={payments} money={money} />

      {invoice.source === "storefront" && <ShopperCard invoice={invoice} />}

      <section className="invoice-section" aria-labelledby="notes-heading">
        <h2 id="notes-heading">Notes</h2>
        {actions.editNotes ? (
          <NotesEditor invoice={invoice} />
        ) : (
          <p>{invoice.notes ? invoice.notes : <span className="muted">No notes.</span>}</p>
        )}
      </section>

      <StatusActions invoice={invoice} actions={actions} money={money} />
    </>
  );
}

export function InvoicePage() {
  const { invoiceId } = useParams();
  const { state } = useAuth();
  const role = state.status === "signedIn" ? state.claims?.appRole : undefined;
  const invoice = useInvoice(invoiceId);

  if (invoice.isPending) return <Loading label="Loading invoice…" />;
  if (invoice.isError) {
    const notFound = invoice.error instanceof ApiError && invoice.error.status === 404;
    return notFound ? (
      <>
        <PageHeader title="Invoice not found" />
        <Link to="/invoices" className="btn-text">
          Back to invoices
        </Link>
      </>
    ) : (
      <ErrorState title="The invoice couldn't be loaded" error={invoice.error} onRetry={() => void invoice.refetch()} />
    );
  }
  return (
    <InvoiceView
      invoice={invoice.data}
      actions={invoiceActions(invoice.data, role)}
      payments={paymentActions(invoice.data, role)}
      canLinkCustomer={role === "owner" || role === "staff"}
    />
  );
}
