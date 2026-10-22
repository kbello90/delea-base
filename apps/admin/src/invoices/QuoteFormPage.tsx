import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { ErrorState } from "../components/ErrorState";
import { Field } from "../components/Field";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { useToast } from "../components/toastContext";
import {
  documentNumber,
  statusLabel,
  useCreateInvoice,
  useInvoice,
  usePreview,
  useReplaceItems,
  type Invoice,
  type PreviewRequest,
} from "./api";
import { CartTable, ProductPicker, StockShortages, Totals } from "./Cart";
import { taxOverride, useCart, type CartLine } from "./cartState";
import { CustomerPicker, type PickedCustomer } from "./CustomerPicker";
import { invoiceActions } from "./invoiceActions";
import "./invoices.css";

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Please try again.");

export function NewQuotePage() {
  const { state } = useAuth();
  const isOwner = state.status === "signedIn" && state.claims?.appRole === "owner";
  const navigate = useNavigate();
  const toast = useToast();
  const cart = useCart();
  const [customer, setCustomer] = useState<PickedCustomer | null>(null);
  const [notes, setNotes] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [taxRaw, setTaxRaw] = useState("");
  const tax = taxOverride(taxRaw);
  const request: PreviewRequest | null =
    cart.valid && !tax.error
      ? { customer_id: customer?.id ?? null, items: cart.items, tax_rate_bps: isOwner ? tax.bps : null }
      : null;
  const preview = usePreview(request);
  const create = useCreateInvoice();

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!request) return;
    create.mutate(
      { ...request, notes: notes.trim() ? notes : null, due_date: dueDate || null, pay_now: false },
      {
        onSuccess: (quote) => {
          toast({ title: `${documentNumber(quote)} was created`, variant: "success" });
          navigate(`/invoices/${quote.id}`);
        },
      },
    );
  }

  return (
    <>
      <PageHeader title="New quote" description="Prices, tax and totals come from the server; nothing is charged." />
      <form className="record-form quote-form" onSubmit={submit} noValidate>
        {create.isError && (
          <p className="form-error" role="alert">
            {message(create.error)}
          </p>
        )}
        <fieldset>
          <legend>Customer</legend>
          <CustomerPicker id="quote-customer" value={customer} onChange={setCustomer} allowWalkIn />
        </fieldset>
        <fieldset>
          <legend>Items</legend>
          <ProductPicker onAdd={cart.add} />
          <CartTable cart={cart} preview={preview.data} pricing={preview.isFetching} busy={create.isPending} />
          {preview.isError && (
            <p className="form-error" role="alert">
              {message(preview.error)}
            </p>
          )}
          <Totals totals={preview.data} pending={preview.isFetching} />
          <StockShortages preview={preview.data} />
          {Boolean(preview.data?.stock_shortages.length) && (
            <p className="field-hint">A quote can be saved anyway; the stock is needed when it's paid.</p>
          )}
        </fieldset>
        <fieldset>
          <legend>Details</legend>
          <div className="form-grid">
            <Field id="due-date" label="Due date" hint="Leave empty to use the customer's payment terms.">
              <input
                id="due-date"
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
                aria-describedby="due-date-hint"
              />
            </Field>
            {isOwner && (
              <Field id="tax-override" label="Tax rate override (%)" hint="Leave empty to use your default rate." error={tax.error}>
                <input
                  id="tax-override"
                  type="text"
                  inputMode="decimal"
                  value={taxRaw}
                  onChange={(e) => setTaxRaw(e.target.value)}
                  aria-invalid={Boolean(tax.error)}
                  aria-describedby={tax.error ? "tax-override-error" : "tax-override-hint"}
                />
              </Field>
            )}
          </div>
          <Field id="notes" label="Notes">
            <textarea id="notes" rows={3} maxLength={5000} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>
        </fieldset>
        <div className="form-actions">
          <Button type="submit" disabled={!request || create.isPending}>
            {create.isPending ? "Saving…" : "Save quote"}
          </Button>
          <Link to="/invoices" className="btn-text">
            Cancel
          </Link>
        </div>
      </form>
    </>
  );
}

function linesOf(invoice: Invoice): CartLine[] {
  const merged = new Map<string, CartLine>();
  for (const line of invoice.items ?? []) {
    if (!line.product_id) continue;
    const seen = merged.get(line.product_id);
    if (seen) seen.qty += line.qty;
    else merged.set(line.product_id, { product: { id: line.product_id, name: line.description, sku: "" }, qty: line.qty });
  }
  return [...merged.values()];
}

function EditLines({ invoice }: { invoice: Invoice }) {
  const navigate = useNavigate();
  const toast = useToast();
  const cart = useCart(linesOf(invoice));
  const request: PreviewRequest | null = cart.valid ? { items: cart.items } : null;
  const preview = usePreview(request, invoice.id);
  const replace = useReplaceItems(invoice.id);
  const number = documentNumber(invoice);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!request) return;
    replace.mutate(request.items, {
      onSuccess: () => {
        toast({ title: `${number} was updated`, variant: "success" });
        navigate(`/invoices/${invoice.id}`);
      },
    });
  }

  return (
    <>
      <PageHeader title={`Edit lines of ${number}`} description={`For ${invoice.customer_name}. Prices come from the server.`} />
      <form className="record-form quote-form" onSubmit={submit} noValidate>
        {replace.isError && (
          <p className="form-error" role="alert">
            {message(replace.error)}
          </p>
        )}
        <fieldset>
          <legend>Items</legend>
          <ProductPicker onAdd={cart.add} />
          <CartTable cart={cart} preview={preview.data} pricing={preview.isFetching} busy={replace.isPending} />
          {preview.isError && (
            <p className="form-error" role="alert">
              {message(preview.error)}
            </p>
          )}
          <Totals totals={preview.data} pending={preview.isFetching} />
          <StockShortages preview={preview.data} />
        </fieldset>
        <div className="form-actions">
          <Button type="submit" disabled={!request || replace.isPending}>
            {replace.isPending ? "Saving…" : "Save lines"}
          </Button>
          <Link to={`/invoices/${invoice.id}`} className="btn-text">
            Cancel
          </Link>
        </div>
      </form>
    </>
  );
}

export function EditQuoteLinesPage() {
  const { invoiceId } = useParams();
  const { state } = useAuth();
  const role = state.status === "signedIn" ? state.claims?.appRole : undefined;
  const invoice = useInvoice(invoiceId);

  if (invoice.isPending) return <Loading label="Loading quote…" />;
  if (invoice.isError) {
    return <ErrorState title="The quote couldn't be loaded" error={invoice.error} onRetry={() => void invoice.refetch()} />;
  }
  const data = invoice.data;
  if (!invoiceActions(data, role).editLines) {
    const reason =
      data.order_status !== "quote"
        ? `Only quotes can be edited. ${documentNumber(data)} is ${statusLabel(data.order_status).toLowerCase()}, so its lines can't change.`
        : `${documentNumber(data)} is cancelled, so its lines can't change.`;
    return (
      <>
        <PageHeader title="These lines can't be edited" />
        <p className="notice" role="status">
          {reason}
        </p>
        <Link to={`/invoices/${data.id}`} className="btn-text">
          Back to {documentNumber(data)}
        </Link>
      </>
    );
  }
  return <EditLines invoice={data} />;
}
