import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { PageHeader } from "../components/PageHeader";
import { SearchBox } from "../components/SearchBox";
import { customerName, useCustomer } from "../customers/api";
import { formatMoney } from "../lib/money";
import { useCurrency } from "../lib/useCurrency";
import {
  documentNumber,
  STATUSES,
  statusLabel,
  useCsvExport,
  useInvoiceList,
  type Invoice,
  type InvoiceFilters,
  type InvoiceSource,
  type OrderStatus,
  SOURCES,
  useInvoiceCounts,
} from "./api";
import { CustomerPicker } from "./CustomerPicker";
import { formatDate } from "./format";
import "./invoices.css";

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function readFilters(params: URLSearchParams): InvoiceFilters {
  const status = params.get("status");
  const customer = params.get("customer");
  const from = params.get("from");
  const to = params.get("to");
  const page = Number.parseInt(params.get("page") ?? "1", 10);
  return {
    status: STATUSES.some((s) => s.value === status) ? (status as OrderStatus) : null,
    customerId: customer && UUID.test(customer) ? customer : null,
    dateFrom: from && DAY.test(from) ? from : null,
    dateTo: to && DAY.test(to) ? to : null,
    q: params.get("q") ?? "",
    source: SOURCES.some((s) => s.value === params.get("source")) ? (params.get("source") as InvoiceSource) : null,
    awaiting: params.get("awaiting") === "1",
    page: Number.isFinite(page) && page > 0 ? page : 1,
  };
}

function useFilterParams() {
  const [params, setParams] = useSearchParams();
  const filters = readFilters(params);
  /** Set or clear a filter; any filter change goes back to page 1. */
  const set = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value === null || value === "") next.delete(key);
        else next.set(key, value);
        if (key !== "page") next.delete("page");
        return next;
      },
      { replace: key === "q" },
    );
  const clear = () => setParams(new URLSearchParams());
  const active = Boolean(
    filters.q || filters.status || filters.customerId || filters.dateFrom || filters.dateTo || filters.source || filters.awaiting,
  );
  return { filters, set, clear, active };
}

export function StatusTags({ invoice }: { invoice: Pick<Invoice, "order_status" | "refunded_at" | "cancelled_at"> }) {
  return (
    <>
      <span className={invoice.order_status === "quote" ? "tag" : "tag tag--ok"}>{statusLabel(invoice.order_status)}</span>
      {invoice.refunded_at && <span className="tag tag--warn">Refunded</span>}
      {invoice.cancelled_at && <span className="tag tag--warn">Cancelled</span>}
    </>
  );
}

/** The selected customer's name (owner and staff can read customers; the URL holds only the id). */
function CustomerFilter({ id, onChange }: { id: string | null; onChange: (id: string | null) => void }) {
  const customer = useCustomer(id ?? undefined);
  const value = id ? { id, name: customer.data ? customerName(customer.data) : "Selected customer" } : null;
  return <CustomerPicker id="filter-customer" value={value} onChange={(c) => onChange(c?.id ?? null)} />;
}

function InvoiceRow({ invoice, currency }: { invoice: Invoice; currency: string }) {
  const number = documentNumber(invoice);
  return (
    <tr>
      <td>
        <Link to={`/invoices/${invoice.id}`} className="cell-title">
          {number}
        </Link>
        {invoice.invoice_number && <span className="muted customer-contact">{invoice.quote_number}</span>}
      </td>
      <td data-label="Customer">
        {invoice.customer_id === null ? <span className="tag">Walk-in</span> : invoice.customer_name}
      </td>
      <td data-label="Source">
        {invoice.source === "storefront" ? (
          <>
            <span className="tag tag--web">Website</span>
            {invoice.order_ref && <span className="muted customer-contact">{invoice.order_ref}</span>}
          </>
        ) : (
          <span className="tag">Admin</span>
        )}
      </td>
      <td data-label="Created">{formatDate(invoice.created_at)}</td>
      <td data-label="Status">
        <StatusTags invoice={invoice} />
      </td>
      <td data-label="Total" className="num">
        {formatMoney(invoice.total_cents, currency)}
      </td>
    </tr>
  );
}

/** The preview's "Awaiting payment" card: website orders the owner still has to confirm. */
function AwaitingPaymentCard({ active, onShow }: { active: boolean; onShow: () => void }) {
  const counts = useInvoiceCounts(true);
  const n = counts.data?.awaiting_payment_web;
  return (
    <section className="metric-cards" aria-label="Website orders">
      <div className="metric-card">
        <span className="metric-card__label" id="awaiting-label">
          Awaiting payment
        </span>
        <span className="metric-card__value" aria-labelledby="awaiting-label">
          {n ?? "…"}
        </span>
        <span className="metric-card__hint">Website orders to confirm when the payment arrives</span>
        {n !== undefined && n > 0 && !active && (
          <button type="button" className="btn-text" onClick={onShow}>
            Show them
          </button>
        )}
      </div>
    </section>
  );
}

export function InvoicesPage() {
  const { state } = useAuth();
  const role = state.status === "signedIn" ? state.claims?.appRole : undefined;
  const writer = role === "owner" || role === "staff";
  const { filters, set, clear, active } = useFilterParams();
  const list = useInvoiceList(filters);
  const currency = useCurrency();
  const csv = useCsvExport();

  return (
    <>
      <PageHeader
        title="Invoices"
        description={writer ? undefined : "You can view invoices and export them; line items aren't visible to your role."}
        actions={
          <>
            <button type="button" className="btn-text" onClick={() => csv.mutate(filters)} disabled={csv.isPending}>
              {csv.isPending ? "Preparing CSV…" : "Export CSV"}
            </button>
            {writer && (
              <>
                <Link to="/invoices/new" className="btn-text">
                  New quote
                </Link>
                <Link to="/invoices/sell" className="dl-button">
                  New sale
                </Link>
              </>
            )}
          </>
        }
      />
      {writer && <AwaitingPaymentCard active={filters.awaiting} onShow={() => set("awaiting", "1")} />}
      {csv.isError && (
        <p className="form-error" role="alert">
          The CSV couldn't be downloaded: {csv.error.message}
        </p>
      )}

      <form className="filters" role="search" aria-label="Filter invoices" onSubmit={(e) => e.preventDefault()}>
        <SearchBox id="invoice-search" placeholder="QTE-…, INV-… or W-…" value={filters.q} onChange={(q) => set("q", q)} />
        <div className="filter">
          <label htmlFor="filter-source">Source</label>
          <select id="filter-source" value={filters.source ?? ""} onChange={(e) => set("source", e.target.value)}>
            <option value="">All</option>
            {SOURCES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        {filters.awaiting && (
          <span className="tag tag--warn filter-chip">
            Awaiting payment
            <button type="button" className="btn-text" onClick={() => set("awaiting", null)} aria-label="Show all invoices, not only those awaiting payment">
              ×
            </button>
          </span>
        )}
        <div className="filter">
          <label htmlFor="filter-status">Status</label>
          <select id="filter-status" value={filters.status ?? ""} onChange={(e) => set("status", e.target.value)}>
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <div className="filter">
          <label htmlFor="filter-from">Created from (UTC)</label>
          <input id="filter-from" type="date" value={filters.dateFrom ?? ""} onChange={(e) => set("from", e.target.value)} />
        </div>
        <div className="filter">
          <label htmlFor="filter-to">Created to (UTC)</label>
          <input id="filter-to" type="date" value={filters.dateTo ?? ""} onChange={(e) => set("to", e.target.value)} />
        </div>
        {writer && <CustomerFilter id={filters.customerId} onChange={(id) => set("customer", id)} />}
        {active && (
          <button type="button" className="btn-text" onClick={clear}>
            Clear filters
          </button>
        )}
      </form>

      {(list.isPending || currency.status === "pending") && !list.isError && (
        <div className="skeleton skeleton--table" role="status" aria-label="Loading invoices" />
      )}
      {list.isError && <ErrorState title="Invoices couldn't be loaded" error={list.error} onRetry={() => void list.refetch()} />}
      {currency.status === "error" && (
        <ErrorState title="The currency couldn't be loaded" error={currency.error} onRetry={currency.retry} />
      )}
      {list.data &&
        currency.status === "success" &&
        (list.data.items.length === 0 ? (
          active || filters.page > 1 ? (
            <EmptyState
              title="No invoices match these filters"
              action={
                <button type="button" className="btn-text" onClick={clear}>
                  Clear filters
                </button>
              }
            />
          ) : (
            <EmptyState
              title="No invoices yet"
              description={writer ? "Start with a quote, or ring up a sale in the shop." : undefined}
              action={
                writer ? (
                  <Link to="/invoices/sell" className="dl-button">
                    New sale
                  </Link>
                ) : undefined
              }
            />
          )
        ) : (
          <>
            <div className={list.isPlaceholderData ? "table-wrap is-refreshing" : "table-wrap"} aria-busy={list.isFetching}>
              <table className="data-grid">
                <caption className="visually-hidden">Invoices, page {filters.page}</caption>
                <thead>
                  <tr>
                    <th scope="col">Number</th>
                    <th scope="col">Customer</th>
                    <th scope="col">Source</th>
                    <th scope="col">Created (UTC)</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num">
                      Total
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.items.map((invoice) => (
                    <InvoiceRow key={invoice.id} invoice={invoice} currency={currency.currency} />
                  ))}
                </tbody>
              </table>
            </div>
            <nav className="pager" aria-label="Pages">
              <button type="button" className="btn-text" disabled={filters.page <= 1} onClick={() => set("page", String(filters.page - 1))}>
                Previous page
              </button>
              <span>Page {filters.page}</span>
              <button type="button" className="btn-text" disabled={!list.data.hasNext} onClick={() => set("page", String(filters.page + 1))}>
                Next page
              </button>
            </nav>
          </>
        ))}
    </>
  );
}
