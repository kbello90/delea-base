import type { Schemas } from "@delea/api-client";
import { vi } from "vitest";
import { DEFAULT_BRAND_RESPONSE, jsonResponse, TENANT_A } from "../test/harness";

type Customer = Schemas["CustomerOut"];

let counter = 0;

export function makeCustomer(overrides: Partial<Customer> = {}): Customer {
  counter += 1;
  return {
    id: `c0000000-0000-0000-0000-${String(counter).padStart(12, "0")}`,
    tenant_id: TENANT_A,
    customer_type: "retail",
    first_name: `First${counter}`,
    last_name: `Last${counter}`,
    email: null,
    phone: null,
    address_line1: null,
    address_line2: null,
    city: null,
    state: null,
    postal_code: null,
    country: "US",
    marketing_opt_in: false,
    notes: null,
    company_name: null,
    tax_exempt: false,
    resale_certificate_number: null,
    wholesale_discount_bps: 0,
    payment_terms: "due_on_receipt",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    invoice_count: 0,
    ...overrides,
  };
}

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

const WHOLESALE_DEFAULTS = {
  company_name: null,
  tax_exempt: false,
  resale_certificate_number: null,
  wholesale_discount_bps: 0,
  payment_terms: "due_on_receipt",
} as const;

/** The API's rule (backend/app/models/customer.py, wholesale_rule_errors). */
function ruleErrors(c: Customer): string[] {
  const errors = [];
  if (c.customer_type === "retail") {
    const set = Object.entries(WHOLESALE_DEFAULTS).filter(([k, v]) => c[k as keyof Customer] !== v);
    if (set.length) errors.push(`wholesale-only fields are not allowed on a retail customer: ${set.map(([k]) => k).join(", ")}`);
  } else if (!c.company_name) {
    errors.push("a wholesale customer needs a company_name");
  }
  if (c.tax_exempt && !c.resale_certificate_number) errors.push("a tax-exempt customer needs a resale_certificate_number");
  return errors;
}

/**
 * An in-memory customers API: list (q on names, company and email; customer_type;
 * limit/offset; by last name), detail, create and update (422 on the wholesale rule), delete
 * (409 when the customer has invoices, with the API's message).
 */
export function customerServer(initial: Customer[] = []) {
  const customers = new Map(initial.map((c) => [c.id, structuredClone(c)]));
  const calls: Call[] = [];
  const overrides = new Map<string, (call: Call) => Response | undefined>();

  const fetch = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    const text = request.method === "GET" || request.method === "DELETE" ? "" : await request.text();
    const call: Call = { method: request.method, path: url.pathname, query: url.searchParams, body: text ? JSON.parse(text) : undefined };
    calls.push(call);
    const custom = overrides.get(`${call.method} ${call.path}`)?.(call);
    if (custom) return custom;
    return handle(call);
  });

  function handle({ method, path, query, body }: Call): Response {
    if (path === "/api/v1/settings/currency") return jsonResponse(200, { currency: "USD" });
    if (path === "/api/v1/settings/brand") return jsonResponse(200, DEFAULT_BRAND_RESPONSE);

    if (path === "/api/v1/customers" && method === "GET") {
      let rows = [...customers.values()].sort((a, b) =>
        (a.last_name ?? "~").localeCompare(b.last_name ?? "~") || a.first_name.localeCompare(b.first_name),
      );
      const q = query.get("q")?.toLowerCase();
      if (q) {
        rows = rows.filter((c) =>
          [c.first_name, c.last_name, c.company_name, c.email].some((v) => v?.toLowerCase().includes(q)),
        );
      }
      if (query.get("customer_type")) rows = rows.filter((c) => c.customer_type === query.get("customer_type"));
      const offset = Number(query.get("offset") ?? 0);
      const limit = Number(query.get("limit") ?? 50);
      return jsonResponse(200, rows.slice(offset, offset + limit));
    }
    if (path === "/api/v1/customers" && method === "POST") {
      const customer = makeCustomer({ ...(body as Partial<Customer>), invoice_count: 0 });
      const errors = ruleErrors(customer);
      if (errors.length) return jsonResponse(422, { detail: errors.join("; ") });
      customers.set(customer.id, customer);
      return jsonResponse(201, customer);
    }

    const match = /^\/api\/v1\/customers\/([^/]+)$/.exec(path);
    const customer = match && customers.get(match[1]);
    if (!customer) return jsonResponse(404, { detail: "Customer not found" });
    if (method === "GET") return jsonResponse(200, customer);
    if (method === "PATCH") {
      const merged = { ...customer, ...(body as Partial<Customer>), updated_at: new Date().toISOString() };
      const errors = ruleErrors(merged);
      if (errors.length) return jsonResponse(422, { detail: errors.join("; ") });
      customers.set(customer.id, merged);
      return jsonResponse(200, merged);
    }
    if (method === "DELETE") {
      if (customer.invoice_count > 0) {
        return jsonResponse(409, {
          detail: `This customer has ${customer.invoice_count} invoice(s), so they can't be deleted. Their invoices stay linked to them to keep your sales history complete.`,
        });
      }
      customers.delete(customer.id);
      return new Response(null, { status: 204 });
    }
    return jsonResponse(404, { detail: "Not found" });
  }

  return {
    fetch,
    calls,
    customers,
    override: (key: string, responder: (call: Call) => Response | undefined) => overrides.set(key, responder),
    last: (method: string, path: string) => [...calls].reverse().find((c) => c.method === method && c.path === path),
  };
}
