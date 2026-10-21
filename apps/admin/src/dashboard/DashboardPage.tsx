import { unwrap, type Schemas } from "@delea/api-client";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../lib/api";
import { formatMoney } from "../lib/money";
import { useCurrency, type CurrencyState } from "../lib/useCurrency";
import "./dashboard.css";
import { PeriodToggle } from "./PeriodToggle";
import { TrendChart } from "./TrendChart";
import { usePeriod, type Period } from "./usePeriod";

type Summary = Schemas["DashboardSummary"];

const summaryQueryKey = ["dashboard", "summary"] as const;
const monthlyQueryKey = ["dashboard", "monthly"] as const;

const STATUS_LABELS: [keyof Summary["invoice_counts"], string][] = [
  ["quote", "Quote"],
  ["paid", "Paid"],
  ["in_production", "In production"],
  ["shipped", "Shipped"],
  ["delivered", "Delivered"],
  ["refunded", "Refunded"],
  ["cancelled", "Cancelled"],
];

const dayFormat = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

/** Nothing recorded at all yet: no invoices of any status and no sales or expenses this year. */
function isEmptyTenant(s: Summary): boolean {
  const noInvoices = Object.values(s.invoice_counts).every((count) => count === 0);
  return noInvoices && s.ytd.revenue_cents === 0 && s.ytd.expenses_cents === 0;
}

/** `value` null: the amount isn't known yet (currency still loading), so a placeholder is shown. */
function Metric({ id, label, value, note }: { id: string; label: string; value: string | null; note?: string | null }) {
  return (
    <div className="metric" role="group" aria-labelledby={`${id}-label`} aria-busy={value === null}>
      <div className="metric__label" id={`${id}-label`}>
        {label}
      </div>
      {value === null ? (
        <div className="skeleton">
          <div className="skeleton__line skeleton__line--value" />
        </div>
      ) : (
        <div className="metric__value">{value}</div>
      )}
      {note && <div className="metric__note">{note}</div>}
    </div>
  );
}

function Figures({ summary, period, currency }: { summary: Summary; period: Period; currency: string | null }) {
  const p = summary[period];
  // No currency yet: no amount is formatted at all.
  const money = (cents: number) => (currency ? formatMoney(cents, currency) : null);
  const quiet = p.revenue_cents === 0 && p.expenses_cents === 0;
  return (
    <>
      {quiet && (
        <p className="dashboard__quiet" role="status">
          No sales or expenses recorded {period === "mtd" ? "this month" : "this year"} yet.
        </p>
      )}
      <div className="metrics">
        <Metric id="revenue" label="Revenue" value={money(p.revenue_cents)} note="Paid invoices, before tax" />
        <Metric id="tax" label="Tax reserve" value={money(p.tax_reserve_cents)} note="Sales tax collected, not revenue" />
        <Metric
          id="expenses"
          label="Expenses"
          value={money(p.expenses_cents)}
          note={currency ? `${money(p.deductible_expenses_cents)} tax deductible` : null}
        />
        <Metric
          id="net"
          label="Net profit"
          value={money(p.net_profit_cents)}
          note={p.net_profit_cents < 0 ? "Loss: expenses exceed revenue" : "Revenue minus expenses"}
        />
      </div>
    </>
  );
}

function Trend({ currency }: { currency: string }) {
  const api = useApi();
  const monthly = useQuery({
    queryKey: monthlyQueryKey,
    queryFn: () => unwrap(api.GET("/api/v1/dashboard/monthly")),
  });
  return (
    <section className="card" aria-labelledby="trend-title">
      <div className="card__head">
        <h2 id="trend-title">Revenue and expenses, last 6 months</h2>
      </div>
      <div className="card__body">
        {monthly.isPending && <div className="skeleton skeleton--chart" role="status" aria-label="Loading chart" />}
        {monthly.isError && (
          <ErrorState title="The chart couldn't be loaded" error={monthly.error} onRetry={() => void monthly.refetch()} />
        )}
        {monthly.data && <TrendChart months={monthly.data.months} currency={currency} />}
      </div>
    </section>
  );
}

function Skeleton() {
  return (
    <div role="status" aria-label="Loading dashboard">
      <div className="metrics" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="metric skeleton">
            <div className="skeleton__line skeleton__line--short" />
            <div className="skeleton__line skeleton__line--value" />
          </div>
        ))}
      </div>
      <div className="skeleton skeleton--chart" aria-hidden="true" />
    </div>
  );
}

function Money({ summary, period, currency }: { summary: Summary; period: Period; currency: CurrencyState }) {
  if (currency.status === "error") {
    return (
      <ErrorState
        title="Amounts can't be shown"
        error={currency.error}
        message="The business currency couldn't be loaded, so no amounts are shown. Try again in a moment."
        onRetry={currency.retry}
      />
    );
  }
  const code = currency.status === "success" ? currency.currency : null;
  return (
    <>
      <Figures summary={summary} period={period} currency={code} />
      {code ? (
        <Trend currency={code} />
      ) : (
        <div className="skeleton skeleton--chart" role="status" aria-label="Loading chart" />
      )}
    </>
  );
}

export function DashboardPage() {
  const api = useApi();
  const currency = useCurrency();
  const [period, setPeriod] = usePeriod();
  const summary = useQuery({
    queryKey: summaryQueryKey,
    queryFn: () => unwrap(api.GET("/api/v1/dashboard/summary")),
  });
  const s = summary.data;

  return (
    <>
      <PeriodHeader summary={s} period={period} onPeriod={setPeriod} />
      {summary.isPending && <Skeleton />}
      {summary.isError && (
        <ErrorState title="The dashboard couldn't be loaded" error={summary.error} onRetry={() => void summary.refetch()} />
      )}
      {s &&
        (isEmptyTenant(s) ? (
          <EmptyState
            title="No sales or expenses yet"
            description="Figures appear here once you record a paid invoice or an expense."
            action={
              <p className="dashboard__links">
                <Link to="/invoices">Go to invoices</Link> · <Link to="/expenses">Go to expenses</Link>
              </p>
            }
          />
        ) : (
          <Money summary={s} period={period} currency={currency} />
        ))}
      {s && <Details summary={s} />}
    </>
  );
}

function PeriodHeader({
  summary,
  period,
  onPeriod,
}: {
  summary: Summary | undefined;
  period: Period;
  onPeriod: (period: Period) => void;
}) {
  return (
    <PageHeader
      title="Dashboard"
      description={
        summary ? (
          <>
            Periods are UTC. Month to date from {dayFormat.format(new Date(summary.month_start))}; year to date from{" "}
            {dayFormat.format(new Date(summary.year_start))}.
          </>
        ) : (
          "Periods are UTC calendar months and years."
        )
      }
      actions={<PeriodToggle value={period} onChange={onPeriod} />}
    />
  );
}

function Details({ summary }: { summary: Summary }) {
  return (
    <div className="dashboard__details">
      <section className="card" aria-labelledby="attention-title">
        <div className="card__head">
          <h2 id="attention-title">Low stock</h2>
        </div>
        <ul className="card__body stat-list">
          <li>
            <span className="stat-list__count">{summary.low_stock_products}</span>
            <span>{summary.low_stock_products === 1 ? "product" : "products"} below the low-stock threshold</span>
            <Link to="/products">View products</Link>
          </li>
          <li>
            <span className="stat-list__count">{summary.low_stock_materials}</span>
            <span>{summary.low_stock_materials === 1 ? "material" : "materials"} below the reorder threshold</span>
            <Link to="/materials">View materials</Link>
          </li>
        </ul>
      </section>
      <section className="card" aria-labelledby="status-title">
        <div className="card__head">
          <h2 id="status-title">Invoices by status</h2>
        </div>
        <div className="card__body">
          <dl className="status-list">
            {STATUS_LABELS.map(([key, label]) => (
              <div key={key} className="status-list__row">
                <dt>{label}</dt>
                <dd>{summary.invoice_counts[key]}</dd>
              </div>
            ))}
          </dl>
          <p className="card__note">All time. Refunded and cancelled invoices are counted separately and never count as revenue.</p>
        </div>
      </section>
    </div>
  );
}
