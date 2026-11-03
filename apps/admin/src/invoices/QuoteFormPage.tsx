import { ApiError, unwrap } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useQueries } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { ErrorState } from "../components/ErrorState";
import { Field } from "../components/Field";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { useToast } from "../components/toastContext";
import { useApi } from "../lib/api";
import type { Product } from "../products/api";
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
import { lineKey, taxOverride, useCart, type CartLine } from "./cartState";
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
      ? { customer_id: customer?.id ?? null, items: cart.priceItems, tax_rate_bps: isOwner ? tax.bps : null }
      : null;
  const preview = usePreview(request);
  const create = useCreateInvoice();

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!request || !cart.answered) return;
    create.mutate(
      { ...request, items: cart.items, notes: notes.trim() ? notes : null, due_date: dueDate || null, pay_now: false, source: "admin" },
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
          <Button type="submit" disabled={!request || !cart.answered || create.isPending}>
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

/**
 * The quote's lines as a cart. Lines of a product without personalization merge; a
 * personalized line keeps its own answers, matched back to the product's fields by label.
 */
function linesOf(invoice: Invoice, products: Map<string, Product>): CartLine[] {
  const lines: CartLine[] = [];
  for (const line of invoice.items ?? []) {
    if (!line.product_id) continue;
    const product = products.get(line.product_id);
    const fields = product?.personalization ?? [];
    const base = product ?? { id: line.product_id, name: line.description, sku: "" };
    if (fields.length === 0) {
      const seen = lines.find((l) => l.product.id === line.product_id);
      if (seen) seen.qty += line.qty;
      else lines.push({ key: lineKey(), product: base, qty: line.qty, answers: {} });
      continue;
    }
    const answers: Record<string, string> = {};
    for (const a of line.personalization ?? []) {
      const field = fields.find((f) => f.label.toLowerCase() === a.label.toLowerCase());
      if (field) answers[field.id] = a.value;
    }
    lines.push({ key: lineKey(), product: base, qty: line.qty, answers });
  }
  return lines;
}

function EditLines({ invoice, products }: { invoice: Invoice; products: Map<string, Product> }) {
  const navigate = useNavigate();
  const toast = useToast();
  const cart = useCart(linesOf(invoice, products));
  const request: PreviewRequest | null = cart.valid ? { items: cart.priceItems } : null;
  const preview = usePreview(request, invoice.id);
  const replace = useReplaceItems(invoice.id);
  const number = documentNumber(invoice);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!request || !cart.answered) return;
    replace.mutate(cart.items, {
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
          <Button type="submit" disabled={!request || !cart.answered || replace.isPending}>
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
  const products = useLineProducts(invoice.data);

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
  if (products.pending) return <Loading label="Loading quote…" />;
  return <EditLines invoice={data} products={products.byId} />;
}

/** The quote's products, for their personalization questions (a deleted one is left out). */
function useLineProducts(invoice: Invoice | undefined) {
  const api = useApi();
  const ids = [...new Set((invoice?.items ?? []).flatMap((l) => (l.product_id ? [l.product_id] : [])))];
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["products", "detail", id],
      queryFn: () => unwrap(api.GET("/api/v1/products/{product_id}", { params: { path: { product_id: id } } })),
      retry: false,
    })),
  });
  const byId = new Map<string, Product>();
  for (const r of results) if (r.data) byId.set(r.data.id, r.data);
  return { pending: results.some((r) => r.isPending), byId };
}
