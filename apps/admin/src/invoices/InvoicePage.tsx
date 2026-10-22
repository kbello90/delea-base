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
import { useCurrency } from "../lib/useCurrency";
import {
  documentNumber,
  methodLabel,
  PAYMENT_METHODS,
  statusLabel,
  useCancel,
  useChangeStatus,
  useDeleteInvoice,
  useInvoice,
  useRefund,
  useUpdateInvoice,
  type Invoice,
  type OrderStatus,
  type PaymentMethod,
} from "./api";
import { Totals } from "./Cart";
import { formatDateTime, formatDay } from "./format";
import { invoiceActions, type InvoiceActions } from "./invoiceActions";
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

function StatusActions({ invoice, actions }: { invoice: Invoice; actions: InvoiceActions }) {
  const number = documentNumber(invoice);
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

  function open(next: Pending) {
    change.reset();
    refund.reset();
    cancel.reset();
    remove.reset();
    setMethod(null);
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
          title: `Mark ${number} paid?`,
          description: (
            <>
              <p>This gives it an invoice number and takes its items out of stock. It can't be undone.</p>
              <PaymentMethodChoice name="mark-paid-method" value={method} onChange={setMethod} disabled={busy} />
            </>
          ),
          confirmLabel: "Mark paid",
        };
      case "status":
        return {
          title: `Mark ${number} ${statusLabel(pending.status).toLowerCase()}?`,
          description: <p>The status only moves forward, so this can't be undone.</p>,
          confirmLabel: `Mark ${statusLabel(pending.status).toLowerCase()}`,
        };
      case "refund":
        return {
          title: `Refund ${number}?`,
          description: <p>It's marked refunded and its status stops changing. Stock isn't put back.</p>,
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
            Mark paid
          </Button>
        )}
        {actions.forward.map((status) => (
          <Button key={status} type="button" variant="ghost" onClick={() => open({ kind: "status", status })}>
            Mark {statusLabel(status).toLowerCase()}
          </Button>
        ))}
        {actions.refund && (
          <button type="button" className="btn-text btn-text--danger" onClick={() => open({ kind: "refund" })}>
            Refund
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
  const shown = invoice.payment_method ? methodLabel(invoice.payment_method) : <span className="muted">Not recorded</span>;
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

function InvoiceView({ invoice, actions, canLinkCustomer }: { invoice: Invoice; actions: InvoiceActions; canLinkCustomer: boolean }) {
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
                ) : invoice.payment_method ? (
                  methodLabel(invoice.payment_method)
                ) : (
                  <span className="muted">Not recorded</span>
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
                    <td className="cell-title">{line.description}</td>
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

      <section className="invoice-section" aria-labelledby="notes-heading">
        <h2 id="notes-heading">Notes</h2>
        {actions.editNotes ? (
          <NotesEditor invoice={invoice} />
        ) : (
          <p>{invoice.notes ? invoice.notes : <span className="muted">No notes.</span>}</p>
        )}
      </section>

      <StatusActions invoice={invoice} actions={actions} />
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
      canLinkCustomer={role === "owner" || role === "staff"}
    />
  );
}
