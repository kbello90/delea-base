import type { Schemas } from "@delea/api-client";
import { useEffect, useRef, useState } from "react";
import { formatMoney } from "../lib/money";
import { niceTicks } from "./chartScale";

type Month = Schemas["MonthTotals"];

const HEIGHT = 240;
const MARGIN = { top: 12, right: 8, bottom: 28, left: 52 };
const MAX_BAR = 24; // dataviz: bars never wider than 24px
const GAP = 2; // surface gap between the two bars of a month
const RADIUS = 4; // rounded data end, square at the baseline

const monthShort = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" });
const monthLong = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

function asDate(isoDay: string): Date {
  return new Date(`${isoDay}T00:00:00Z`);
}

function compactMoney(cents: number, currency: string): string {
  // Ticks are whole currency units (multiples of 100 cents), so this division is exact.
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(cents / 100);
}

/** A column with a rounded top and a square base. */
function barPath(x: number, y: number, w: number, h: number): string {
  const r = Math.min(RADIUS, h, w / 2);
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

function useWidth<T extends HTMLElement>(fallback: number) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, Math.floor(entry.contentRect.width))));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/**
 * Revenue and expenses, last 6 months: grouped columns on one axis (both are money).
 * Colors are fixed series colors (validated for color-vision deficiency on both themes),
 * not the tenant's brand, so revenue and expenses always look the same. The legend names
 * both series; the table is the text alternative and is always in the page.
 */
export function TrendChart({ months, currency }: { months: Month[]; currency: string }) {
  const [containerRef, width] = useWidth<HTMLDivElement>(640);
  const [active, setActive] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);

  const ticks = niceTicks(Math.max(0, ...months.flatMap((m) => [m.revenue_cents, m.expenses_cents])));
  const top = ticks[ticks.length - 1];
  const plotW = width - MARGIN.left - MARGIN.right;
  const plotH = HEIGHT - MARGIN.top - MARGIN.bottom;
  const band = plotW / Math.max(1, months.length);
  const barW = Math.min(MAX_BAR, Math.max(4, (band * 0.6 - GAP) / 2));
  const groupW = barW * 2 + GAP;
  const y = (cents: number) => MARGIN.top + plotH - (cents / top) * plotH;
  const latest = months[months.length - 1];

  const label = (m: Month) =>
    `${monthLong.format(asDate(m.month_start))}: revenue ${formatMoney(m.revenue_cents, currency)}, expenses ${formatMoney(m.expenses_cents, currency)}`;

  return (
    <div className="trend" ref={containerRef}>
      <div className="trend__header">
        <ul className="trend__legend" aria-label="Legend">
          <li>
            <span className="swatch swatch--revenue" aria-hidden="true" />
            Revenue
            {latest && <span className="trend__latest">{monthShort.format(asDate(latest.month_start))}: {formatMoney(latest.revenue_cents, currency)}</span>}
          </li>
          <li>
            <span className="swatch swatch--expenses" aria-hidden="true" />
            Expenses
            {latest && <span className="trend__latest">{monthShort.format(asDate(latest.month_start))}: {formatMoney(latest.expenses_cents, currency)}</span>}
          </li>
        </ul>
        <button type="button" className="btn-text" aria-pressed={showTable} onClick={() => setShowTable((s) => !s)}>
          {showTable ? "View as chart" : "View as table"}
        </button>
      </div>

      {!showTable && (
      <div className="trend__plot">
        <svg width={width} height={HEIGHT} role="group" aria-label="Revenue and expenses by month, last 6 months">
          {ticks.map((t) => (
            <g key={t} aria-hidden="true">
              <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(t)} y2={y(t)} className="trend__grid" />
              <text x={MARGIN.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="trend__tick">
                {compactMoney(t, currency)}
              </text>
            </g>
          ))}
          {months.map((m, i) => {
            const x = MARGIN.left + band * i + (band - groupW) / 2;
            const revenueH = y(0) - y(m.revenue_cents);
            const expensesH = y(0) - y(m.expenses_cents);
            return (
              <g
                key={m.month_start}
                tabIndex={0}
                role="img"
                aria-label={label(m)}
                className={active === i ? "trend__month is-active" : "trend__month"}
                onPointerEnter={() => setActive(i)}
                onPointerLeave={() => setActive(null)}
                onFocus={() => setActive(i)}
                onBlur={() => setActive(null)}
              >
                {/* The whole month band is the hit target, bigger than the bars. */}
                <rect x={MARGIN.left + band * i} y={MARGIN.top} width={band} height={plotH} className="trend__hit" />
                {revenueH > 0 && <path d={barPath(x, y(m.revenue_cents), barW, revenueH)} className="bar bar--revenue" />}
                {expensesH > 0 && <path d={barPath(x + barW + GAP, y(m.expenses_cents), barW, expensesH)} className="bar bar--expenses" />}
                <text x={MARGIN.left + band * i + band / 2} y={HEIGHT - 8} textAnchor="middle" className="trend__tick" aria-hidden="true">
                  {monthShort.format(asDate(m.month_start))}
                </text>
              </g>
            );
          })}
        </svg>
        {active !== null && months[active] && (
          <div
            className="trend__tooltip"
            role="presentation"
            style={{ left: Math.min(Math.max(MARGIN.left + band * active + band / 2, 90), width - 90) }}
          >
            <div className="trend__tooltip-month">{monthLong.format(asDate(months[active].month_start))}</div>
            <div>
              <span className="line-key line-key--revenue" aria-hidden="true" />
              <strong>{formatMoney(months[active].revenue_cents, currency)}</strong> Revenue
            </div>
            <div>
              <span className="line-key line-key--expenses" aria-hidden="true" />
              <strong>{formatMoney(months[active].expenses_cents, currency)}</strong> Expenses
            </div>
          </div>
        )}
      </div>
      )}

      <table className={showTable ? "data-table" : "data-table visually-hidden"}>
        <caption>Revenue and expenses by month, last 6 months (UTC)</caption>
        <thead>
          <tr>
            <th scope="col">Month</th>
            <th scope="col">Revenue</th>
            <th scope="col">Expenses</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => (
            <tr key={m.month_start}>
              <th scope="row">{monthLong.format(asDate(m.month_start))}</th>
              <td>{formatMoney(m.revenue_cents, currency)}</td>
              <td>{formatMoney(m.expenses_cents, currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
