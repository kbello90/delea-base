import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { PageHeader } from "../components/PageHeader";
import { SearchBox } from "../components/SearchBox";
import { formatMoney } from "../lib/money";
import { useCurrency } from "../lib/useCurrency";
import {
  CATEGORIES,
  categoryLabel,
  useCsvExport,
  useExpenseList,
  useExpenseSummary,
  useViewReceipt,
  type Expense,
  type ExpenseCategory,
  type ExpenseFilters,
} from "./api";
import { DeleteExpenseDialog } from "./DeleteExpenseDialog";
import { formatDay } from "./expenseForm";
import "./expenses.css";

const DAY = /^\d{4}-\d{2}-\d{2}$/;

function readFilters(params: URLSearchParams): ExpenseFilters {
  const category = params.get("category");
  const deductible = params.get("deductible");
  const from = params.get("from");
  const to = params.get("to");
  const page = Number.parseInt(params.get("page") ?? "1", 10);
  return {
    dateFrom: from && DAY.test(from) ? from : null,
    dateTo: to && DAY.test(to) ? to : null,
    category: CATEGORIES.some((c) => c.value === category) ? (category as ExpenseCategory) : null,
    deductible: deductible === "yes" ? true : deductible === "no" ? false : null,
    q: params.get("q") ?? "",
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
  const active = Boolean(filters.q || filters.category || filters.dateFrom || filters.dateTo || filters.deductible !== null);
  return { filters, set, clear, active };
}

function ExpenseRow({
  expense,
  currency,
  isOwner,
  onDelete,
  onView,
  viewing,
}: {
  expense: Expense;
  currency: string;
  isOwner: boolean;
  onDelete: (e: Expense) => void;
  onView: (e: Expense) => void;
  viewing: boolean;
}) {
  return (
    <tr>
      <td data-label="Date">{formatDay(expense.expense_date)}</td>
      <td>
        <Link to={`/expenses/${expense.id}`} className="cell-title">
          {expense.description}
        </Link>
      </td>
      <td data-label="Category">{categoryLabel(expense.category)}</td>
      <td data-label="Deductible">
        {expense.is_tax_deductible ? <span className="tag tag--ok">Deductible</span> : <span className="muted">Not deductible</span>}
      </td>
      <td data-label="Receipt">
        {expense.has_receipt ? (
          <button
            type="button"
            className="btn-text"
            onClick={() => onView(expense)}
            disabled={viewing}
            aria-label={`View receipt for ${expense.description}`}
          >
            View receipt
          </button>
        ) : (
          <span className="muted">None</span>
        )}
      </td>
      <td data-label="Amount" className="num">
        {formatMoney(expense.amount_cents, currency)}
      </td>
      {isOwner && (
        <td className="row-actions">
          <Link to={`/expenses/${expense.id}`} className="btn-text" aria-label={`Edit ${expense.description}`}>
            Edit
          </Link>
          <button
            type="button"
            className="btn-text btn-text--danger"
            aria-label={`Delete ${expense.description}`}
            onClick={() => onDelete(expense)}
          >
            Delete
          </button>
        </td>
      )}
    </tr>
  );
}

export function ExpensesPage() {
  const { state } = useAuth();
  const isOwner = state.status === "signedIn" && state.claims?.appRole === "owner";
  const { filters, set, clear, active } = useFilterParams();
  const list = useExpenseList(filters);
  const summary = useExpenseSummary(filters);
  const currency = useCurrency();
  const csv = useCsvExport();
  const view = useViewReceipt();
  const [deleting, setDeleting] = useState<Expense | null>(null);

  return (
    <>
      <PageHeader
        title="Expenses"
        description={isOwner ? undefined : "You can view expenses and receipts and export them; only the owner can change them."}
        actions={
          <>
            <button type="button" className="btn-text" onClick={() => csv.mutate(filters)} disabled={csv.isPending}>
              {csv.isPending ? "Preparing CSV…" : "Export CSV"}
            </button>
            {isOwner && (
              <Link to="/expenses/new" className="dl-button">
                New expense
              </Link>
            )}
          </>
        }
      />
      {csv.isError && (
        <p className="form-error" role="alert">
          The CSV couldn't be downloaded: {csv.error.message}
        </p>
      )}
      {view.isError && (
        <p className="form-error" role="alert">
          The receipt couldn't be opened: {view.error.message}
        </p>
      )}

      <form className="filters" role="search" aria-label="Filter expenses" onSubmit={(e) => e.preventDefault()}>
        <SearchBox id="expense-search" placeholder="Description" value={filters.q} onChange={(q) => set("q", q)} />
        <div className="filter">
          <label htmlFor="filter-category">Category</label>
          <select id="filter-category" value={filters.category ?? ""} onChange={(e) => set("category", e.target.value)}>
            <option value="">All</option>
            {CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
        <div className="filter">
          <label htmlFor="filter-deductible">Deductible</label>
          <select
            id="filter-deductible"
            value={filters.deductible === null ? "" : filters.deductible ? "yes" : "no"}
            onChange={(e) => set("deductible", e.target.value)}
          >
            <option value="">All</option>
            <option value="yes">Deductible</option>
            <option value="no">Not deductible</option>
          </select>
        </div>
        <div className="filter">
          <label htmlFor="filter-from">From</label>
          <input id="filter-from" type="date" value={filters.dateFrom ?? ""} onChange={(e) => set("from", e.target.value)} />
        </div>
        <div className="filter">
          <label htmlFor="filter-to">To</label>
          <input id="filter-to" type="date" value={filters.dateTo ?? ""} onChange={(e) => set("to", e.target.value)} />
        </div>
        {active && (
          <button type="button" className="btn-text" onClick={clear}>
            Clear filters
          </button>
        )}
      </form>

      {summary.data && currency.status === "success" && (
        <dl className="expense-totals" aria-label="Totals for these filters" aria-busy={summary.isFetching}>
          <div>
            <dt>Expenses</dt>
            <dd>{summary.data.count}</dd>
          </div>
          <div>
            <dt>Total</dt>
            <dd>{formatMoney(summary.data.total_cents, currency.currency)}</dd>
          </div>
          <div>
            <dt>Deductible</dt>
            <dd>{formatMoney(summary.data.deductible_cents, currency.currency)}</dd>
          </div>
        </dl>
      )}
      {summary.isError && <ErrorState title="The totals couldn't be loaded" error={summary.error} onRetry={() => void summary.refetch()} />}

      {(list.isPending || currency.status === "pending") && !list.isError && (
        <div className="skeleton skeleton--table" role="status" aria-label="Loading expenses" />
      )}
      {list.isError && <ErrorState title="Expenses couldn't be loaded" error={list.error} onRetry={() => void list.refetch()} />}
      {currency.status === "error" && (
        <ErrorState title="The currency couldn't be loaded" error={currency.error} onRetry={currency.retry} />
      )}
      {list.data &&
        currency.status === "success" &&
        (list.data.items.length === 0 ? (
          active || filters.page > 1 ? (
            <EmptyState
              title="No expenses match these filters"
              action={
                <button type="button" className="btn-text" onClick={clear}>
                  Clear filters
                </button>
              }
            />
          ) : (
            <EmptyState
              title="No expenses yet"
              description={isOwner ? "Record what the business spends, with a photo of the receipt." : undefined}
              action={
                isOwner ? (
                  <Link to="/expenses/new" className="dl-button">
                    Add an expense
                  </Link>
                ) : undefined
              }
            />
          )
        ) : (
          <>
            <div className={list.isPlaceholderData ? "table-wrap is-refreshing" : "table-wrap"} aria-busy={list.isFetching}>
              <table className="data-grid">
                <caption className="visually-hidden">Expenses, page {filters.page}</caption>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Description</th>
                    <th scope="col">Category</th>
                    <th scope="col">Deductible</th>
                    <th scope="col">Receipt</th>
                    <th scope="col" className="num">
                      Amount
                    </th>
                    {isOwner && (
                      <th scope="col">
                        <span className="visually-hidden">Actions</span>
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {list.data.items.map((expense) => (
                    <ExpenseRow
                      key={expense.id}
                      expense={expense}
                      currency={currency.currency}
                      isOwner={isOwner}
                      onDelete={setDeleting}
                      onView={(e) => view.mutate(e.id)}
                      viewing={view.isPending}
                    />
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

      {deleting && <DeleteExpenseDialog expense={deleting} open onOpenChange={(open) => !open && setDeleting(null)} />}
    </>
  );
}
