import type { Schemas } from "@delea/api-client";
import { vi } from "vitest";
import { DEFAULT_BRAND_RESPONSE, jsonResponse, TENANT_A } from "../test/harness";

type Material = Schemas["MaterialOut"];

let counter = 0;

/** Quantities are exact two-decimal strings, as the API returns them. */
export function makeMaterial(overrides: Partial<Material> = {}): Material {
  counter += 1;
  const material: Material = {
    id: `b0000000-0000-0000-0000-${String(counter).padStart(12, "0")}`,
    tenant_id: TENANT_A,
    name: `Material ${counter}`,
    unit: "pcs",
    unit_cost_cents: 250,
    qty_on_hand: "10.00",
    reorder_threshold: "2.00",
    is_low: false,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
  return { ...material, is_low: Number(material.qty_on_hand) < Number(material.reorder_threshold) };
}

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

/**
 * An in-memory materials API: list (q, low_stock, limit/offset; ordered by name), create and
 * update (409 on a duplicate name), delete (409 when `usedBy` has the id).
 */
export function materialServer(initial: Material[] = [], options: { usedBy?: Set<string>; currency?: string } = {}) {
  const materials = new Map(initial.map((m) => [m.id, structuredClone(m)]));
  const calls: Call[] = [];
  const overrides = new Map<string, (call: Call) => Response | undefined>();
  const duplicateName = (name: unknown, except?: string) =>
    [...materials.values()].some((m) => m.name === name && m.id !== except);

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
    if (path === "/api/v1/settings/currency") return jsonResponse(200, { currency: options.currency ?? "USD" });
    if (path === "/api/v1/settings/brand") return jsonResponse(200, DEFAULT_BRAND_RESPONSE);

    if (path === "/api/v1/materials" && method === "GET") {
      let rows = [...materials.values()].sort((a, b) => a.name.localeCompare(b.name));
      const q = query.get("q")?.toLowerCase();
      if (q) rows = rows.filter((m) => m.name.toLowerCase().includes(q));
      if (query.get("low_stock") === "true") rows = rows.filter((m) => m.is_low);
      const offset = Number(query.get("offset") ?? 0);
      const limit = Number(query.get("limit") ?? 50);
      return jsonResponse(200, rows.slice(offset, offset + limit));
    }
    if (path === "/api/v1/materials" && method === "POST") {
      const input = body as Partial<Material>;
      if (duplicateName(input.name)) return jsonResponse(409, { detail: "A record with these values already exists" });
      const material = makeMaterial(input);
      materials.set(material.id, material);
      return jsonResponse(201, material);
    }

    const match = /^\/api\/v1\/materials\/([^/]+)$/.exec(path);
    const material = match && materials.get(match[1]);
    if (!material) return jsonResponse(404, { detail: "Material not found" });
    if (method === "GET") return jsonResponse(200, material);
    if (method === "PATCH") {
      const input = body as Partial<Material>;
      if (input.name !== undefined && duplicateName(input.name, material.id)) {
        return jsonResponse(409, { detail: "A record with these values already exists" });
      }
      const saved = makeMaterial({ ...material, ...input, updated_at: new Date().toISOString() });
      materials.set(material.id, saved);
      return jsonResponse(200, saved);
    }
    if (method === "DELETE") {
      if (options.usedBy?.has(material.id)) {
        return jsonResponse(409, { detail: "This material is used by 2 product(s); remove it from those products first." });
      }
      materials.delete(material.id);
      return new Response(null, { status: 204 });
    }
    return jsonResponse(404, { detail: "Not found" });
  }

  return {
    fetch,
    calls,
    materials,
    /** Answer one method + path differently (return undefined to fall through). */
    override: (key: string, responder: (call: Call) => Response | undefined) => overrides.set(key, responder),
    last: (method: string, path: string) => [...calls].reverse().find((c) => c.method === method && c.path === path),
  };
}
