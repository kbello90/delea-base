import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { PageHeader } from "../components/PageHeader";
import { SearchBox } from "../components/SearchBox";
import { customerName, termsLabel, useCustomerList, type Customer, type CustomerFilters, type CustomerType } from "./api";
import { bpsToPercent, looksLikeContactDetails } from "./customerForm";
import { DeleteCustomerDialog } from "./DeleteCustomerDialog";

const TYPES: { value: CustomerType; label: string }[] = [
  { value: "retail", label: "Retail" },
  { value: "wholesale", label: "Wholesale" },
];

function readFilters(params: URLSearchParams): CustomerFilters {
  const type = params.get("type");
  const page = Number.parseInt(params.get("page") ?? "1", 10);
  return {
    q: params.get("q") ?? "",
    type: TYPES.some((t) => t.value === type) ? (type as CustomerType) : null,
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
  const active = Boolean(filters.q || filters.type);
  return { filters, set, clear, active };
}

function CustomerRow({ customer, isOwner, onDelete }: { customer: Customer; isOwner: boolean; onDelete: (c: Customer) => void }) {
  const name = customerName(customer);
  const wholesale = customer.customer_type === "wholesale";
  const contact = [customer.first_name, customer.last_name].filter(Boolean).join(" ");
  return (
    <tr>
      <td>
        <Link to={`/customers/${customer.id}`} className="cell-title">
          {name}
        </Link>
        {wholesale && <span className="muted customer-contact">Contact: {contact}</span>}
      </td>
      <td data-label="Type">
        <span className={wholesale ? "tag tag--ok" : "tag"}>{wholesale ? "Wholesale" : "Retail"}</span>
        {customer.tax_exempt && <span className="tag">Tax-exempt</span>}
      </td>
      <td data-label="Terms">
        {wholesale ? (
          <>
            {termsLabel(customer.payment_terms)}
            {customer.wholesale_discount_bps > 0 && (
              <span className="muted"> · {bpsToPercent(customer.wholesale_discount_bps)}% off</span>
            )}
          </>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td data-label="Invoices">{customer.invoice_count}</td>
      <td className="row-actions">
        <Link to={`/customers/${customer.id}`} className="btn-text" aria-label={`Edit ${name}`}>
          Edit
        </Link>
        {isOwner && (
          <button type="button" className="btn-text btn-text--danger" aria-label={`Delete ${name}`} onClick={() => onDelete(customer)}>
            Delete
          </button>
        )}
      </td>
    </tr>
  );
}

export function CustomersPage() {
  const { state } = useAuth();
  const isOwner = state.status === "signedIn" && state.claims?.appRole === "owner";
  const { filters, set, clear, active } = useFilterParams();
  const list = useCustomerList(filters);
  const [deleting, setDeleting] = useState<Customer | null>(null);
  const [contactSearch, setContactSearch] = useState(false);


  return (
    <>
      <PageHeader
        title="Customers"
        description={isOwner ? undefined : "You can add and edit customers; only the owner can delete them."}
        actions={
          <Link to="/customers/new" className="dl-button">
            New customer
          </Link>
        }
      />

      <form className="filters" role="search" aria-label="Filter customers" onSubmit={(e) => e.preventDefault()}>
        <SearchBox
          id="customer-search"
          placeholder="Name or company"
          value={filters.q}
          onChange={(q) => set("q", q)}
          refuse={looksLikeContactDetails}
          onRefused={setContactSearch}
        />
        <div className="filter">
          <label htmlFor="filter-type">Type</label>
          <select id="filter-type" value={filters.type ?? ""} onChange={(e) => set("type", e.target.value)}>
            <option value="">All</option>
            {TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
        {active && (
          <button type="button" className="btn-text" onClick={clear}>
            Clear filters
          </button>
        )}
      </form>
      {contactSearch && (
        <p className="field-hint search-hint" role="status">
          Search by name or company. Email addresses and phone numbers aren't searched here, so they never end up in a
          web address.
        </p>
      )}

      {list.isPending && <div className="skeleton skeleton--table" role="status" aria-label="Loading customers" />}
      {list.isError && <ErrorState title="Customers couldn't be loaded" error={list.error} onRetry={() => void list.refetch()} />}
      {list.data &&
        (list.data.items.length === 0 ? (
          active || filters.page > 1 ? (
            <EmptyState
              title="No customers match these filters"
              action={
                <button type="button" className="btn-text" onClick={clear}>
                  Clear filters
                </button>
              }
            />
          ) : (
            <EmptyState
              title="No customers yet"
              description="Add the people and shops you sell to, so quotes and invoices can use their details."
              action={
                <Link to="/customers/new" className="dl-button">
                  Add a customer
                </Link>
              }
            />
          )
        ) : (
          <>
            <div className={list.isPlaceholderData ? "table-wrap is-refreshing" : "table-wrap"} aria-busy={list.isFetching}>
              <table className="data-grid">
                <caption className="visually-hidden">Customers, page {filters.page}</caption>
                <thead>
                  <tr>
                    <th scope="col">Customer</th>
                    <th scope="col">Type</th>
                    <th scope="col">Terms</th>
                    <th scope="col">Invoices</th>
                    <th scope="col">
                      <span className="visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.items.map((customer) => (
                    <CustomerRow key={customer.id} customer={customer} isOwner={isOwner} onDelete={setDeleting} />
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

      {deleting && <DeleteCustomerDialog customer={deleting} open onOpenChange={(open) => !open && setDeleting(null)} />}
    </>
  );
}
