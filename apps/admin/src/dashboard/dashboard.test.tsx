import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import {
  fakeApi,
  fakeBackend,
  jsonResponse,
  MONTHLY_RESPONSE,
  renderApp,
  SUMMARY_RESPONSE,
  tokenFor,
} from "../test/harness";

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

/** The value shown in the metric card with this label. */
function metric(label: string) {
  return within(screen.getByRole("group", { name: label }));
}

async function renderDashboard(routes: Parameters<typeof fakeApi>[0] = {}) {
  const fetch = fakeApi(routes);
  const utils = renderApp({ path: "/dashboard", fetch });
  await screen.findByRole("group", { name: "Revenue" });
  return { ...utils, fetch };
}

const zeroPeriod = {
  revenue_cents: 0,
  tax_reserve_cents: 0,
  expenses_cents: 0,
  deductible_expenses_cents: 0,
  net_profit_cents: 0,
};

describe("dashboard figures", () => {
  it("shows month-to-date figures from the API by default", async () => {
    await renderDashboard();
    expect(screen.getByRole("radio", { name: "Month to date" })).toBeChecked();
    expect(metric("Revenue").getByText("$98.00")).toBeInTheDocument();
    expect(metric("Tax reserve").getByText("$7.84")).toBeInTheDocument();
    expect(metric("Expenses").getByText("$913.00")).toBeInTheDocument();
    expect(metric("Expenses").getByText("$845.00 tax deductible")).toBeInTheDocument();
    expect(metric("Net profit").getByText("-$815.00")).toBeInTheDocument();
    expect(metric("Net profit").getByText(/^Loss/)).toBeInTheDocument();
  });

  it("the toggle is a radiogroup that switches to year to date", async () => {
    await renderDashboard();
    const group = screen.getByRole("group", { name: "Period" });
    expect(within(group).getAllByRole("radio")).toHaveLength(2);

    await userEvent.setup().click(screen.getByRole("radio", { name: "Year to date" }));
    expect(screen.getByRole("radio", { name: "Year to date" })).toBeChecked();
    expect(metric("Revenue").getByText("$12,345.00")).toBeInTheDocument();
    expect(metric("Tax reserve").getByText("$987.60")).toBeInTheDocument();
    expect(metric("Expenses").getByText("$4,567.00")).toBeInTheDocument();
    expect(metric("Net profit").getByText("$7,778.00")).toBeInTheDocument();
    expect(screen.queryByText("$98.00", { selector: ".metric__value" })).not.toBeInTheDocument();
  });

  it("arrow keys move between periods", async () => {
    await renderDashboard();
    const user = userEvent.setup();
    screen.getByRole("radio", { name: "Month to date" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Year to date" })).toBeChecked();
    expect(metric("Revenue").getByText("$12,345.00")).toBeInTheDocument();
  });

  it("remembers the period for this tab only (sessionStorage, not localStorage)", async () => {
    const first = await renderDashboard();
    await userEvent.setup().click(screen.getByRole("radio", { name: "Year to date" }));
    expect(sessionStorage.getItem("delea.dashboard.period")).toBe("ytd");
    expect(localStorage.length).toBe(0);
    first.unmount();

    await renderDashboard();
    expect(screen.getByRole("radio", { name: "Year to date" })).toBeChecked();
    expect(metric("Revenue").getByText("$12,345.00")).toBeInTheDocument();
  });

  it("says that periods are UTC, with the period starts", async () => {
    await renderDashboard();
    expect(screen.getByText(/Periods are UTC\. Month to date from Oct 1, 2026; year to date from Jan 1, 2026\./)).toBeInTheDocument();
  });

  it("formats in the tenant's currency from GET /settings/currency", async () => {
    const { fetch } = await renderDashboard({ "/api/v1/settings/currency": () => jsonResponse(200, { currency: "EUR" }) });
    expect(await metric("Revenue").findByText("€98.00")).toBeInTheDocument();
    expect(metric("Net profit").getByText("-€815.00")).toBeInTheDocument();
    expect(screen.queryByText(/\$\d/)).not.toBeInTheDocument();
    const paths = fetch.mock.calls.map(([request]) => new URL(request.url).pathname);
    expect(paths).not.toContain("/api/v1/settings/company");
  });

  it("links the low-stock counts and lists every invoice status", async () => {
    await renderDashboard();
    const lowStock = within(screen.getByRole("region", { name: "Low stock" }));
    expect(lowStock.getByText("product below the low-stock threshold")).toBeInTheDocument();
    expect(lowStock.getByText("materials below the reorder threshold")).toBeInTheDocument();
    expect(lowStock.getByRole("link", { name: "View products" })).toHaveAttribute("href", "/products");
    expect(lowStock.getByRole("link", { name: "View materials" })).toHaveAttribute("href", "/materials");

    const statuses = within(screen.getByRole("region", { name: "Invoices by status" }));
    const rows = statuses.getAllByRole("term").map((dt) => [dt.textContent, dt.nextElementSibling?.textContent]);
    expect(rows).toEqual([
      ["Quote", "1"],
      ["Paid", "1"],
      ["In production", "0"],
      ["Shipped", "3"],
      ["Delivered", "4"],
      ["Refunded", "5"],
      ["Cancelled", "6"],
    ]);
  });
});

describe("trend chart", () => {
  it("has a text alternative: a table of all six months", async () => {
    await renderDashboard();
    const table = await screen.findByRole("table", { name: /Revenue and expenses by month/ });
    const rows = within(table)
      .getAllByRole("row")
      .slice(1)
      .map((row) => [...row.querySelectorAll("th, td")].map((c) => c.textContent));
    expect(rows).toEqual([
      ["May 2026", "$0.00", "$0.00"],
      ["June 2026", "$2,500.00", "$1,200.00"],
      ["July 2026", "$3,100.00", "$999.00"],
      ["August 2026", "$0.00", "$450.00"],
      ["September 2026", "$6,647.00", "$1,005.00"],
      ["October 2026", "$98.00", "$913.00"],
    ]);
  });

  it("names both series in a legend and labels each month for keyboard users", async () => {
    await renderDashboard();
    const legend = within(await screen.findByRole("list", { name: "Legend" }));
    expect(legend.getByText("Revenue")).toBeInTheDocument();
    expect(legend.getByText("Expenses")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Revenue and expenses by month, last 6 months" })).toBeInTheDocument();
    const october = screen.getByRole("img", { name: "October 2026: revenue $98.00, expenses $913.00" });
    expect(october).toHaveAttribute("tabindex", "0");

    october.focus();
    expect(await screen.findByText("October 2026", { selector: ".trend__tooltip-month" })).toBeInTheDocument();
  });

  it("can switch to a visible table", async () => {
    await renderDashboard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "View as table" }));
    expect(screen.queryByRole("group", { name: /by month, last 6 months/ })).not.toBeInTheDocument();
    expect(screen.getByRole("table")).not.toHaveClass("visually-hidden");
    await user.click(screen.getByRole("button", { name: "View as chart" }));
    expect(screen.getByRole("group", { name: /by month, last 6 months/ })).toBeInTheDocument();
  });

  it("a chart error stays inside the chart card, with retry", async () => {
    // Fails (through the automatic retries) until the user retries.
    let recovered = false;
    await renderDashboard({
      "/api/v1/dashboard/monthly": () =>
        recovered ? jsonResponse(200, MONTHLY_RESPONSE) : jsonResponse(503, { detail: "Database is temporarily unavailable" }),
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The chart couldn't be loaded");
    expect(metric("Revenue").getByText("$98.00")).toBeInTheDocument();
    recovered = true;
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table", { name: /Revenue and expenses by month/ })).toBeInTheDocument();
  });
});

describe("dashboard states", () => {
  it("shows a loading skeleton first", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    renderApp({
      path: "/dashboard",
      fetch: fakeApi({
        "/api/v1/dashboard/summary": async () => {
          await gate;
          return jsonResponse(200, SUMMARY_RESPONSE);
        },
      }),
    });
    expect(await screen.findByRole("status", { name: "Loading dashboard" })).toBeInTheDocument();
    release();
    expect(await screen.findByRole("group", { name: "Revenue" })).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Loading dashboard" })).not.toBeInTheDocument();
  });

  it("shows the server's error with a working retry", async () => {
    let recovered = false;
    renderApp({
      path: "/dashboard",
      fetch: fakeApi({
        "/api/v1/dashboard/summary": () =>
          recovered ? jsonResponse(200, SUMMARY_RESPONSE) : jsonResponse(503, { detail: "Database is temporarily unavailable" }),
      }),
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The dashboard couldn't be loaded");
    expect(alert).toHaveTextContent("Database is temporarily unavailable");
    recovered = true;
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("group", { name: "Revenue" })).toBeInTheDocument();
    expect(metric("Revenue").getByText("$98.00")).toBeInTheDocument();
  });

  it("an empty tenant gets an honest empty state, not a wall of zeros", async () => {
    const empty = {
      ...SUMMARY_RESPONSE,
      mtd: zeroPeriod,
      ytd: zeroPeriod,
      low_stock_products: 0,
      low_stock_materials: 0,
      invoice_counts: { quote: 0, paid: 0, in_production: 0, shipped: 0, delivered: 0, refunded: 0, cancelled: 0 },
    };
    const fetch = fakeApi({ "/api/v1/dashboard/summary": () => jsonResponse(200, empty) });
    renderApp({ path: "/dashboard", fetch });
    expect(await screen.findByRole("heading", { name: "No sales or expenses yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to invoices" })).toHaveAttribute("href", "/invoices");
    expect(screen.queryByRole("group", { name: "Revenue" })).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("a quiet month says so, while the year still has figures", async () => {
    await renderDashboard({ "/api/v1/dashboard/summary": () => jsonResponse(200, { ...SUMMARY_RESPONSE, mtd: zeroPeriod }) });
    expect(screen.getByText("No sales or expenses recorded this month yet.")).toBeInTheDocument();
    expect(metric("Revenue").getByText("$0.00")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("radio", { name: "Year to date" }));
    expect(screen.queryByText(/recorded this/)).not.toBeInTheDocument();
  });

  it("staff never load the dashboard data", async () => {
    const fetch = fakeApi();
    renderApp({ path: "/dashboard", backend: fakeBackend(tokenFor("staff")), fetch });
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("You don't have access to this page");
    const paths = fetch.mock.calls.map(([request]) => new URL(request.url).pathname);
    expect(paths).not.toContain("/api/v1/dashboard/summary");
  });
});

describe("currency states: money is never formatted without a known currency", () => {
  it("while the currency loads, the money figures are skeletons and no amount is shown", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    renderApp({
      path: "/dashboard",
      fetch: fakeApi({
        "/api/v1/settings/currency": async () => {
          await gate;
          return jsonResponse(200, { currency: "EUR" });
        },
      }),
    });
    const revenue = await screen.findByRole("group", { name: "Revenue" });
    expect(revenue).toHaveAttribute("aria-busy", "true");
    expect(document.querySelectorAll(".metric__value")).toHaveLength(0);
    expect(screen.queryByText(/[$€]\d/)).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading chart" })).toBeInTheDocument();
    // Non-money figures don't wait for the currency.
    expect(screen.getByRole("region", { name: "Invoices by status" })).toBeInTheDocument();

    release();
    expect(await metric("Revenue").findByText("€98.00")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Revenue" })).toHaveAttribute("aria-busy", "false");
  });

  it.each([
    [403, "not a member of this tenant with this role"], // the function refusing a stale membership
    [500, "Internal server error"],
  ])("a %i on the currency shows a message with retry and no amounts", async (status, detail) => {
    let recovered = false;
    renderApp({
      path: "/dashboard",
      fetch: fakeApi({
        "/api/v1/settings/currency": () =>
          recovered ? jsonResponse(200, { currency: "GBP" }) : jsonResponse(status, { detail }),
      }),
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Amounts can't be shown");
    expect(alert).toHaveTextContent("The business currency couldn't be loaded, so no amounts are shown.");
    expect(screen.queryByRole("group", { name: "Revenue" })).not.toBeInTheDocument();
    expect(screen.queryByText(/[$€£]\d/)).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    recovered = true;
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await metric("Revenue").findByText("£98.00")).toBeInTheDocument();
  });
});
