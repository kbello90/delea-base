import type { AppRole } from "@delea/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";
import { expenseServer, FakeXhr, makeExpense } from "./testServer";

const CHAIN = makeExpense({ description: "Chain and findings", category: "materials", amount_cents: 24550, expense_date: "2026-10-03" });
const FEES = makeExpense({
  description: "Platform fees",
  category: "platform_fees",
  amount_cents: 4510,
  expense_date: "2026-10-01",
  has_receipt: true,
  receipt_content_type: "application/pdf",
  receipt_size_bytes: 52_000,
  receipt_uploaded_at: "2026-10-02T09:00:00Z",
});
const LUNCH = makeExpense({ description: "Coffee with a supplier", category: "meals", amount_cents: 2400, is_tax_deductible: false, expense_date: "2026-09-12" });

function setup(path = "/expenses", { role = "owner", expenses = [CHAIN, FEES, LUNCH] }: { role?: AppRole; expenses?: ReturnType<typeof makeExpense>[] } = {}) {
  const server = expenseServer(expenses);
  const utils = renderApp({ path, fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { ...utils, server, user: userEvent.setup() };
}

const rowOf = async (name: string) => within((await screen.findByRole("link", { name })).closest("tr")!);

beforeEach(() => {
  FakeXhr.instances = [];
  FakeXhr.status = 200;
  FakeXhr.delayMs = 10;
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------
describe("expenses list", () => {
  it("shows date, description, plain-English category, deductible, receipt and amount", async () => {
    setup();
    const chain = await rowOf("Chain and findings");
    expect(chain.getByText("Oct 3, 2026")).toBeInTheDocument();
    expect(chain.getByText("Materials")).toBeInTheDocument();
    expect(chain.getByText("Deductible")).toBeInTheDocument();
    expect(chain.getByText("None")).toBeInTheDocument();
    expect(chain.getByText("$245.50")).toBeInTheDocument();
    const fees = await rowOf("Platform fees");
    expect(fees.getByText("Platform and payment fees")).toBeInTheDocument();
    expect(fees.getByRole("button", { name: "View receipt for Platform fees" })).toBeInTheDocument();
    const lunch = await rowOf("Coffee with a supplier");
    expect(lunch.getByText("Not deductible")).toBeInTheDocument();
  });

  it("totals come from the server's summary and cover every page, not the visible one", async () => {
    // 30 expenses of $1.00 to $30.00: page 1 shows 25, the totals cover all 30.
    const many = Array.from({ length: 30 }, (_, n) =>
      makeExpense({ description: `IT row ${n + 1}`, amount_cents: (n + 1) * 100, is_tax_deductible: n % 2 === 0, expense_date: `2026-08-${String(n + 1).padStart(2, "0")}` }),
    );
    const { server } = setup("/expenses", { expenses: many });
    const totals = within(await screen.findByLabelText("Totals for these filters"));
    expect(screen.getAllByRole("row")).toHaveLength(26); // header + 25
    expect(totals.getByText("30")).toBeInTheDocument();
    expect(totals.getByText("$465.00")).toBeInTheDocument(); // 1 + ... + 30
    expect(totals.getByText("$225.00")).toBeInTheDocument(); // 1 + 3 + ... + 29
    expect(server.last("GET", "/api/v1/expenses/summary")).toBeDefined();
  });

  it("filters are kept in the URL and the list and totals get exactly the same ones", async () => {
    const { server, location, user } = setup();
    await rowOf("Chain and findings");
    await user.selectOptions(screen.getByLabelText("Category"), "materials");
    await user.selectOptions(screen.getByLabelText("Deductible"), "yes");
    await user.type(screen.getByLabelText("From"), "2026-10-01");
    await user.type(screen.getByLabelText("To"), "2026-10-31");
    await user.type(screen.getByLabelText("Search"), "chain");
    const expected = { date_from: "2026-10-01", date_to: "2026-10-31", category: "materials", tax_deductible: "true", q: "chain" };
    await waitFor(() => expect(server.filterQuery(server.last("GET", "/api/v1/expenses")!)).toEqual(expected));
    await waitFor(() => expect(server.filterQuery(server.last("GET", "/api/v1/expenses/summary")!)).toEqual(expected));
    expect(location.search).toContain("category=materials");
    expect(location.search).toContain("deductible=yes");
    expect(location.search).toContain("from=2026-10-01");
    expect(await screen.findByRole("link", { name: "Chain and findings" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Platform fees" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(location.search).toBe(""));
  });

  it("pages through 25 at a time", async () => {
    const many = Array.from({ length: 26 }, (_, n) => makeExpense({ description: `IT page ${n + 1}`, expense_date: `2026-07-${String(n + 1).padStart(2, "0")}` }));
    const { server, location, user } = setup("/expenses", { expenses: many });
    await screen.findByRole("link", { name: "IT page 26" });
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(server.last("GET", "/api/v1/expenses")!.query.get("offset")).toBe("25"));
    expect(location.search).toBe("?page=2");
    expect(await screen.findByRole("link", { name: "IT page 1" })).toBeInTheDocument();
  });

  it("shows an empty state and a load error", async () => {
    const { server, user } = setup("/expenses", { expenses: [] });
    expect(await screen.findByText("No expenses yet")).toBeInTheDocument();
    server.override("GET /api/v1/expenses", () => jsonResponse(503, { detail: "The service is temporarily unavailable." }));
    await user.selectOptions(screen.getByLabelText("Category"), "rent");
    expect(await screen.findByText("Expenses couldn't be loaded")).toBeInTheDocument();
  });

  it("exports the CSV for the current filters through an authorised request (cpa too)", async () => {
    const blobs: Blob[] = [];
    URL.createObjectURL = vi.fn((b: Blob) => (blobs.push(b), "blob:x"));
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const { server, user } = setup("/expenses?category=materials&deductible=no", { role: "cpa" });
    await screen.findByRole("heading", { level: 1, name: "Expenses" });
    await user.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(blobs).toHaveLength(1));
    expect(server.filterQuery(server.last("GET", "/api/v1/expenses/export.csv")!)).toEqual({ category: "materials", tax_deductible: "false" });
  });
});

// ---------------------------------------------------------------------------
// Roles
// ---------------------------------------------------------------------------
const WRITE_CONTROLS = [
  ["link", "New expense"],
  ["link", "Edit Chain and findings"],
  ["button", "Delete Chain and findings"],
] as const;

describe("roles", () => {
  it.each([
    ["owner", true],
    ["cpa", false],
  ] as const)("%s on the list: write controls shown = %s; export and receipts always", async (role, writes) => {
    setup("/expenses", { role });
    await rowOf("Chain and findings");
    for (const [kind, name] of WRITE_CONTROLS) {
      expect(Boolean(screen.queryByRole(kind, { name }))).toBe(writes);
    }
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View receipt for Platform fees" })).toBeInTheDocument();
  });

  it("cpa's record page has no form, upload, replace, remove or delete; only view", async () => {
    setup(`/expenses/${FEES.id}`, { role: "cpa" });
    expect(await screen.findByRole("heading", { level: 1, name: "Platform fees" })).toBeInTheDocument();
    expect(screen.getByText("Platform and payment fees")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/receipt/i, { selector: "input" })).not.toBeInTheDocument();
    for (const name of ["Save changes", "Remove receipt", "Delete expense"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "View receipt" })).toBeInTheDocument();
  });

  it("cpa can't open the new-expense page; staff can't open expenses at all", async () => {
    setup("/expenses/new", { role: "cpa" });
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("You don't have access to this page");
  });

  it("staff are refused", async () => {
    setup("/expenses", { role: "staff" });
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("You don't have access to this page");
  });
});

// ---------------------------------------------------------------------------
// Create, edit, delete
// ---------------------------------------------------------------------------
describe("new expense", () => {
  it("validates before sending anything", async () => {
    const { server, user } = setup("/expenses/new");
    await user.click(await screen.findByRole("button", { name: "Save expense" }));
    expect(screen.getByText("Enter the date of the expense.")).toBeInTheDocument();
    expect(screen.getByText("Describe the expense.")).toBeInTheDocument();
    expect(screen.getByText("Enter an amount.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Amount"), "12.345");
    await user.click(screen.getByRole("button", { name: "Save expense" }));
    expect(screen.getByText("Use at most 2 decimal places.")).toBeInTheDocument();
    expect(server.calls.filter((c) => c.method === "POST")).toEqual([]);
  });

  it("creates the expense with its fields only, attaches the receipt, then opens it", async () => {
    const { server, location, user } = setup("/expenses/new");
    await user.type(await screen.findByLabelText("Date"), "2026-10-20");
    await user.type(screen.getByLabelText("Description"), "Bubble mailers");
    await user.selectOptions(screen.getByLabelText("Category"), "packaging");
    await user.type(screen.getByLabelText("Amount"), "1,234.50");
    await user.click(screen.getByLabelText("Tax-deductible"));
    await user.upload(screen.getByLabelText("Receipt file"), new File([new Uint8Array(500)], "mailers.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Save expense" }));
    await waitFor(() => expect(location.pathname).toMatch(/^\/expenses\/e0/));
    expect(server.last("POST", "/api/v1/expenses")!.body).toEqual({
      expense_date: "2026-10-20",
      description: "Bubble mailers",
      category: "packaging",
      amount_cents: 123450,
      is_tax_deductible: false,
    });
    const id = location.pathname.split("/")[2];
    expect(server.last("POST", `/api/v1/expenses/${id}/receipt/uploads`)!.body).toEqual({ content_type: "application/pdf" });
    expect(FakeXhr.instances.map((x) => [x.method, x.headers["Content-Type"], "Authorization" in x.headers])).toEqual([
      ["PUT", "application/pdf", false],
    ]);
    expect(server.last("POST", `/api/v1/expenses/${id}/receipt`)!.body).toEqual({
      upload_id: "u0000000-0000-4000-8000-000000000001",
      content_type: "application/pdf",
    });
    expect(await screen.findByText(/PDF · 2 KB/)).toBeInTheDocument();
  });

  it("refuses a wrong type or an oversize receipt before any request", async () => {
    const { server } = setup("/expenses/new");
    // Ignore the input's accept list, as a user picking "All files" can.
    const user = userEvent.setup({ applyAccept: false });
    const input = await screen.findByLabelText("Receipt file");
    await user.upload(input, new File([new Uint8Array(10)], "photo.heic", { type: "image/heic" }));
    expect(await screen.findByText("photo.heic can't be used: choose a JPEG, PNG or WebP photo, or a PDF.")).toBeInTheDocument();
    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], "scan.pdf", { type: "application/pdf" });
    await user.upload(input, big);
    expect(await screen.findByText("scan.pdf is larger than 10 MB.")).toBeInTheDocument();
    expect(server.calls.filter((c) => c.path.includes("/receipt"))).toEqual([]);
  });
});

describe("expense record page (owner)", () => {
  it("sends only the changed fields", async () => {
    const { server, user } = setup(`/expenses/${CHAIN.id}`);
    const amount = await screen.findByLabelText("Amount");
    expect(amount).toHaveValue("245.50");
    await user.clear(amount);
    await user.type(amount, "250");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(server.last("PATCH", `/api/v1/expenses/${CHAIN.id}`)!.body).toEqual({ amount_cents: 25000 }));
  });

  it("views a receipt through a fresh link in a new tab, never in the app's URL", async () => {
    const tab = { opener: {} as unknown, location: { href: "" }, close: vi.fn() };
    const open = vi.spyOn(window, "open").mockImplementation(() => tab as unknown as Window);
    const { server, location, user } = setup(`/expenses/${FEES.id}`);
    await user.click(await screen.findByRole("button", { name: "View receipt" }));
    await waitFor(() => expect(tab.location.href).toMatch(/token=t1$/));
    expect(open).toHaveBeenCalledWith("", "_blank");
    expect(tab.opener).toBeNull();
    await user.click(screen.getByRole("button", { name: "View receipt" }));
    await waitFor(() => expect(tab.location.href).toMatch(/token=t2$/)); // asked again: a new link
    expect(server.calls.filter((c) => c.path === `/api/v1/expenses/${FEES.id}/receipt` && c.method === "GET")).toHaveLength(2);
    expect(location.pathname + location.search).toBe(`/expenses/${FEES.id}`);
  });

  it("replaces and removes a receipt (removal confirms first)", async () => {
    const { server, user } = setup(`/expenses/${FEES.id}`);
    const input = await screen.findByLabelText("Replace the receipt");
    await user.upload(input, new File([new Uint8Array(100)], "new.jpg", { type: "image/jpeg" }));
    expect(await screen.findByText(/JPEG · 2 KB/)).toBeInTheDocument();
    expect(server.last("POST", `/api/v1/expenses/${FEES.id}/receipt`)!.body).toMatchObject({ content_type: "image/jpeg" });

    await user.click(screen.getByRole("button", { name: "Remove receipt" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove receipt" }));
    expect(await screen.findByText("No receipt attached.")).toBeInTheDocument();
    expect(server.last("DELETE", `/api/v1/expenses/${FEES.id}/receipt`)).toBeDefined();
  });

  it("shows the server's message when a receipt is refused", async () => {
    const { server, user } = setup(`/expenses/${CHAIN.id}`);
    server.override(`POST /api/v1/expenses/${CHAIN.id}/receipt`, () =>
      jsonResponse(422, { detail: "The file's content doesn't match its type: it isn't a PDF." }),
    );
    await user.upload(await screen.findByLabelText("Attach a receipt"), new File([new Uint8Array(100)], "fake.pdf", { type: "application/pdf" }));
    expect(await screen.findByText("The file's content doesn't match its type: it isn't a PDF.")).toBeInTheDocument();
    expect(screen.getByText("No receipt attached.")).toBeInTheDocument();
  });

  it("deletes after a confirmation that mentions the receipt", async () => {
    const { server, location, user } = setup(`/expenses/${FEES.id}`);
    await user.click(await screen.findByRole("button", { name: "Delete expense" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Platform fees and its receipt will be deleted. This can't be undone.");
    await user.click(within(dialog).getByRole("button", { name: "Delete expense" }));
    await waitFor(() => expect(location.pathname).toBe("/expenses"));
    expect(server.last("DELETE", `/api/v1/expenses/${FEES.id}`)).toBeDefined();
  });
});
