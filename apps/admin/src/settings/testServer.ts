import type { Schemas } from "@delea/api-client";
import { vi } from "vitest";
import { COMPANY_RESPONSE, DEFAULT_BRAND_RESPONSE, EMAILS_RESPONSE, PAYMENTS_RESPONSE, STOREFRONT_RESPONSE, jsonResponse } from "../test/harness";

export { FakeXhr } from "../products/testServer";

type Company = Schemas["CompanySettingsOut"];
type Brand = Schemas["BrandConfigOut"];
type Storefront = Schemas["StorefrontSettingsOut"];

export const LOGO_BASE = "http://127.0.0.1:55321/storage/v1/object/public/brand-assets";

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

/**
 * An in-memory settings API: company (owner only; the currency and counters are refused),
 * brand (owner writes, staff read) and the logo endpoints. The role comes from the token.
 */
export function settingsServer({
  company = COMPANY_RESPONSE,
  brand = DEFAULT_BRAND_RESPONSE,
  storefront = STOREFRONT_RESPONSE,
  payments = PAYMENTS_RESPONSE,
  emails = EMAILS_RESPONSE,
}: {
  company?: Company;
  brand?: Brand;
  storefront?: Storefront;
  payments?: Schemas["PaymentSettingsOut"];
  emails?: Schemas["EmailSettingsOut"];
} = {}) {
  let emailsRow = structuredClone(emails);
  let companyRow = structuredClone(company);
  let brandRow = structuredClone(brand);
  let storefrontRow = structuredClone(storefront);
  const calls: Call[] = [];
  const overrides = new Map<string, (call: Call) => Response | undefined>();

  const fetch = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    const text = request.method === "GET" || request.method === "DELETE" ? "" : await request.text();
    const call: Call = { method: request.method, path: url.pathname, body: text ? JSON.parse(text) : undefined };
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

  function handle({ method, path, body }: Call, role: string | null): Response {
    const owner = role === "owner";
    if (path === "/api/v1/settings/currency") return jsonResponse(200, { currency: companyRow.currency });
    if (path === "/api/v1/settings/brand" && method === "GET") {
      return owner || role === "staff" ? jsonResponse(200, brandRow) : jsonResponse(403, { detail: "Insufficient role" });
    }
    if (!owner) return jsonResponse(403, { detail: "Insufficient role" });
    if (path === "/api/v1/settings/company" && method === "GET") return jsonResponse(200, companyRow);
    if (path === "/api/v1/settings/payments" && method === "GET") return jsonResponse(200, payments);
    if (path === "/api/v1/settings/emails" && method === "GET") return jsonResponse(200, emailsRow);
    if (path === "/api/v1/settings/emails" && method === "PATCH") {
      emailsRow = { ...emailsRow, ...(body as object) };
      return jsonResponse(200, emailsRow);
    }
    if (path === "/api/v1/settings/emails/reply-to" && method === "POST") {
      emailsRow = { ...emailsRow, reply_to_pending: (body as { email: string }).email };
      return jsonResponse(200, emailsRow);
    }
    if (path === "/api/v1/settings/emails/test" && method === "POST") return jsonResponse(200, { sent_to: "owner@shop.com", status: "queued" });
    if (path === "/api/v1/settings/payments/stripe/onboarding-link" && method === "POST") {
      if (!payments.stripe_configured) return jsonResponse(503, { detail: "Card payments aren't set up on this server yet." });
      return jsonResponse(200, { url: "https://connect.stripe.com/setup/s/acct_test_onboarding" });
    }
    if (path === "/api/v1/settings/company" && method === "PATCH") {
      const b = body as Record<string, unknown>;
      const refused = ["currency", "next_quote_number", "next_invoice_number", "stripe_account_id", "tenant_id"].find((k) => k in b);
      if (refused) return jsonResponse(422, { detail: `body.${refused}: Extra inputs are not permitted` });
      companyRow = { ...companyRow, ...(b as Partial<Company>), updated_at: new Date().toISOString() };
      return jsonResponse(200, companyRow);
    }
    if (path === "/api/v1/settings/brand" && method === "PATCH") {
      brandRow = { ...brandRow, ...(body as Partial<Brand>), updated_at: new Date().toISOString() };
      return jsonResponse(200, brandRow);
    }
    if (path === "/api/v1/settings/storefront" && method === "GET") return jsonResponse(200, storefrontRow);
    if (path === "/api/v1/settings/storefront" && method === "PATCH") {
      storefrontRow = { ...storefrontRow, ...(body as Partial<Storefront>), updated_at: new Date().toISOString() };
      return jsonResponse(200, storefrontRow);
    }
    if (path === "/api/v1/settings/brand/logo/uploads") {
      return jsonResponse(201, { upload_id: "u0000000-0000-4000-8000-000000000002", upload_url: "https://storage.test/upload/brand?token=x", max_bytes: 2 * 1024 * 1024 });
    }
    if (path === "/api/v1/settings/brand/logo" && method === "POST") {
      brandRow = { ...brandRow, logo_url: `${LOGO_BASE}/${brandRow.tenant_id}/logo-${calls.length}.webp` };
      return jsonResponse(200, brandRow);
    }
    if (path === "/api/v1/settings/brand/logo" && method === "DELETE") {
      if (!brandRow.logo_url) return jsonResponse(404, { detail: "There is no logo to remove" });
      brandRow = { ...brandRow, logo_url: null };
      return jsonResponse(200, brandRow);
    }
    return jsonResponse(404, { detail: "Not found" });
  }

  return {
    fetch,
    calls,
    override: (key: string, responder: (call: Call) => Response | undefined) => overrides.set(key, responder),
    last: (method: string, path: string) => [...calls].reverse().find((c) => c.method === method && c.path === path),
  };
}
