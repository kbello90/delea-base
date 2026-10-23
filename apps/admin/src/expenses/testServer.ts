import type { Schemas } from "@delea/api-client";
import { vi } from "vitest";
import { DEFAULT_BRAND_RESPONSE, jsonResponse, TENANT_A } from "../test/harness";

type Expense = Schemas["ExpenseOut"];

export { FakeXhr } from "../products/testServer";

let counter = 0;

export function makeExpense(overrides: Partial<Expense> = {}): Expense {
  counter += 1;
  const n = String(counter).padStart(12, "0");
  return {
    id: `e0000000-0000-0000-0000-${n}`,
    tenant_id: TENANT_A,
    expense_date: "2026-10-10",
    description: `Expense ${counter}`,
    category: "materials",
    amount_cents: 10000,
    is_tax_deductible: true,
    has_receipt: false,
    receipt_content_type: null,
    receipt_size_bytes: null,
    receipt_uploaded_at: null,
    created_at: "2026-10-10T12:00:00Z",
    updated_at: "2026-10-10T12:00:00Z",
    ...overrides,
  };
}

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

const FILTER_KEYS = ["date_from", "date_to", "category", "tax_deductible", "q"] as const;

/**
 * An in-memory expenses API with the backend's rules: filters (shared by list, summary and
 * CSV), owner-only writes, cpa read-only, staff refused, and the receipt endpoints (sign,
 * register, remove, a fresh link per view). The role comes from the token's app_role.
 */
export function expenseServer(initial: Expense[] = []) {
  const expenses = new Map(initial.map((e) => [e.id, structuredClone(e)]));
  const calls: Call[] = [];
  const overrides = new Map<string, (call: Call) => Response | undefined>();
  let links = 0;

  const fetch = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    const text = request.method === "GET" || request.method === "DELETE" ? "" : await request.text();
    const call: Call = { method: request.method, path: url.pathname, query: url.searchParams, body: text ? JSON.parse(text) : undefined };
    calls.push(call);
    const custom = overrides.get(`${call.method} ${call.path}`)?.(call);
    if (custom) return custom;
    return handle(call, roleOf(request));
  });

  function roleOf(request: Request): string | null {
    const part = request.headers.get("Authorization")?.replace(/^Bearer /, "").split(".")[1];
    if (!part) return null;
    return (JSON.parse(atob(part.replace(/-/g, "+").replace(/_/g, "/"))) as { app_role?: string }).app_role ?? null;
  }

  function filtered(query: URLSearchParams): Expense[] {
    let rows = [...expenses.values()].sort((a, b) => b.expense_date.localeCompare(a.expense_date) || a.id.localeCompare(b.id));
    if (query.get("date_from")) rows = rows.filter((e) => e.expense_date >= query.get("date_from")!);
    if (query.get("date_to")) rows = rows.filter((e) => e.expense_date <= query.get("date_to")!);
    if (query.get("category")) rows = rows.filter((e) => e.category === query.get("category"));
    if (query.get("tax_deductible")) rows = rows.filter((e) => String(e.is_tax_deductible) === query.get("tax_deductible"));
    const q = query.get("q")?.toLowerCase();
    if (q) rows = rows.filter((e) => e.description.toLowerCase().includes(q));
    return rows;
  }

  function handle({ method, path, query, body }: Call, role: string | null): Response {
    if (path === "/api/v1/settings/currency") return jsonResponse(200, { currency: "USD" });
    if (path === "/api/v1/settings/brand") return jsonResponse(200, DEFAULT_BRAND_RESPONSE);
    if (role !== "owner" && role !== "cpa") return jsonResponse(403, { detail: "Insufficient role" });
    const owner = role === "owner";

    if (path === "/api/v1/expenses" && method === "GET") {
      const offset = Number(query.get("offset") ?? 0);
      return jsonResponse(200, filtered(query).slice(offset, offset + Number(query.get("limit") ?? 50)));
    }
    if (path === "/api/v1/expenses/summary") {
      const rows = filtered(query);
      return jsonResponse(200, {
        count: rows.length,
        total_cents: rows.reduce((s, e) => s + e.amount_cents, 0),
        deductible_cents: rows.filter((e) => e.is_tax_deductible).reduce((s, e) => s + e.amount_cents, 0),
      });
    }
    if (path === "/api/v1/expenses/export.csv") {
      return new Response("expense_date,description\n", { status: 200, headers: { "Content-Type": "text/csv" } });
    }
    if (path === "/api/v1/expenses" && method === "POST") {
      if (!owner) return jsonResponse(403, { detail: "Insufficient role" });
      const expense = makeExpense({ ...(body as Partial<Expense>), has_receipt: false });
      expenses.set(expense.id, expense);
      return jsonResponse(201, expense);
    }

    const match = /^\/api\/v1\/expenses\/([^/]+)(\/receipt(?:\/uploads)?)?$/.exec(path);
    const expense = match && expenses.get(match[1]);
    if (!expense) return jsonResponse(404, { detail: "Expense not found" });
    const sub = match[2] ?? "";

    if (sub === "" && method === "GET") return jsonResponse(200, expense);
    if (sub === "/receipt" && method === "GET") {
      if (!expense.has_receipt) return jsonResponse(404, { detail: "This expense has no receipt" });
      links += 1;
      return jsonResponse(200, {
        url: `https://storage.test/object/sign/expense-receipts/r.pdf?token=t${links}`,
        expires_in: 60,
        content_type: expense.receipt_content_type,
      });
    }
    if (!owner) return jsonResponse(403, { detail: "Insufficient role" });

    if (sub === "" && method === "PATCH") {
      const merged = { ...expense, ...(body as Partial<Expense>), updated_at: new Date().toISOString() };
      expenses.set(expense.id, merged);
      return jsonResponse(200, merged);
    }
    if (sub === "" && method === "DELETE") {
      expenses.delete(expense.id);
      return new Response(null, { status: 204 });
    }
    if (sub === "/receipt/uploads") {
      return jsonResponse(201, {
        upload_id: "u0000000-0000-4000-8000-000000000001",
        upload_url: `https://storage.test/upload/expense-receipts/${expense.id}?token=x`,
        max_bytes: 10 * 1024 * 1024,
      });
    }
    if (sub === "/receipt" && method === "POST") {
      const b = body as { content_type: Expense["receipt_content_type"] };
      Object.assign(expense, {
        has_receipt: true,
        receipt_content_type: b.content_type,
        receipt_size_bytes: 2048,
        receipt_uploaded_at: "2026-10-23T10:00:00Z",
        updated_at: new Date().toISOString(),
      });
      return jsonResponse(200, expense);
    }
    if (sub === "/receipt" && method === "DELETE") {
      if (!expense.has_receipt) return jsonResponse(404, { detail: "This expense has no receipt" });
      Object.assign(expense, { has_receipt: false, receipt_content_type: null, receipt_size_bytes: null, receipt_uploaded_at: null });
      return jsonResponse(200, expense);
    }
    return jsonResponse(404, { detail: "Not found" });
  }

  return {
    fetch,
    calls,
    expenses,
    override: (key: string, responder: (call: Call) => Response | undefined) => overrides.set(key, responder),
    last: (method: string, path: string) => [...calls].reverse().find((c) => c.method === method && c.path === path),
    filterQuery: (call: Call) => Object.fromEntries(FILTER_KEYS.flatMap((k) => (call.query.has(k) ? [[k, call.query.get(k)]] : []))),
  };
}
