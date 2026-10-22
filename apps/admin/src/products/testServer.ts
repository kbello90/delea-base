import type { Schemas } from "@delea/api-client";
import { vi } from "vitest";
import { DEFAULT_BRAND_RESPONSE, jsonResponse, TENANT_A } from "../test/harness";

type Product = Schemas["ProductOut"];
type Image = Schemas["ProductImageOut"];

let counter = 0;
const id = (prefix: string) => `${prefix}0000000-0000-0000-0000-${String(++counter).padStart(12, "0")}`;

export function makeImage(position: number, overrides: Partial<Image> = {}): Image {
  const imageId = overrides.id ?? id("e");
  return {
    id: imageId,
    position,
    is_primary: position === 0,
    url: `https://img.test/${imageId}-1200.webp`,
    thumb_url: `https://img.test/${imageId}-400.webp`,
    width: 1200,
    height: 800,
    ...overrides,
  };
}

export function makeProduct(overrides: Partial<Product> = {}): Product {
  return {
    id: id("a"),
    tenant_id: TENANT_A,
    sku: `SKU-${counter}`,
    name: `Product ${counter}`,
    description: null,
    metal: "silver_925",
    price_cents: 12500,
    sale_price_cents: null,
    sale_ends_at: null,
    stock_qty: 10,
    low_stock_threshold: 3,
    icon: null,
    is_published: true,
    sort_order: 0,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    images: [],
    ...overrides,
  };
}

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

/**
 * An in-memory products API: list (q, metal, is_published, low_stock, limit/offset), detail,
 * create (409 on a duplicate SKU), update, delete (409 when `onInvoice`), and the image routes.
 */
export function productServer(initial: Product[] = [], options: { onInvoice?: Set<string>; currency?: string } = {}) {
  const products = new Map(initial.map((p) => [p.id, structuredClone(p)]));
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
    if (path === "/api/v1/settings/currency") return jsonResponse(200, { currency: options.currency ?? "USD" });
    if (path === "/api/v1/settings/brand") return jsonResponse(200, DEFAULT_BRAND_RESPONSE);

    if (path === "/api/v1/products" && method === "GET") {
      let rows = [...products.values()];
      const q = query.get("q")?.toLowerCase();
      if (q) rows = rows.filter((p) => p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q));
      if (query.get("metal")) rows = rows.filter((p) => p.metal === query.get("metal"));
      if (query.get("is_published")) rows = rows.filter((p) => String(p.is_published) === query.get("is_published"));
      if (query.get("low_stock") === "true") rows = rows.filter((p) => p.stock_qty < p.low_stock_threshold);
      const offset = Number(query.get("offset") ?? 0);
      const limit = Number(query.get("limit") ?? 50);
      return jsonResponse(200, rows.slice(offset, offset + limit));
    }
    if (path === "/api/v1/products" && method === "POST") {
      const input = body as Partial<Product>;
      if ([...products.values()].some((p) => p.sku === input.sku)) {
        return jsonResponse(409, { detail: "A record with these values already exists" });
      }
      const product = makeProduct({ ...input, images: [] });
      products.set(product.id, product);
      return jsonResponse(201, product);
    }

    const match = /^\/api\/v1\/products\/([^/]+)(\/images(?:\/(uploads|order|[^/]+)(?:\/(primary))?)?)?$/.exec(path);
    if (!match) return jsonResponse(404, { detail: "Not found" });
    const [, productId, imagesPart, sub, primary] = match;
    const product = products.get(productId);
    if (!product) return jsonResponse(404, { detail: "Product not found" });

    if (!imagesPart) {
      if (method === "GET") return jsonResponse(200, product);
      if (method === "PATCH") {
        Object.assign(product, body, { updated_at: new Date().toISOString() });
        return jsonResponse(200, product);
      }
      if (method === "DELETE") {
        if (options.onInvoice?.has(productId)) {
          return jsonResponse(409, {
            detail: "This product appears on 2 invoice line(s), so it can't be deleted. Unpublish it instead to hide it from your store while keeping your sales history.",
          });
        }
        products.delete(productId);
        return new Response(null, { status: 204 });
      }
    }
    if (sub === "uploads" && method === "POST") {
      const uploadId = id("f");
      return jsonResponse(201, { upload_id: uploadId, upload_url: `https://storage.test/upload/${uploadId}`, max_bytes: 5242880 });
    }
    if (imagesPart === "/images" && method === "POST") {
      const image = makeImage(product.images.length, { is_primary: product.images.length === 0 });
      product.images.push(image);
      return jsonResponse(201, image);
    }
    if (sub === "order" && method === "PUT") {
      const ids = (body as { image_ids: string[] }).image_ids;
      product.images = ids.map((imageId, position) => ({ ...product.images.find((i) => i.id === imageId)!, position }));
      return jsonResponse(200, product);
    }
    if (primary && method === "POST") {
      product.images = product.images.map((i) => ({ ...i, is_primary: i.id === sub }));
      return jsonResponse(200, product);
    }
    if (sub && method === "DELETE") {
      const removed = product.images.find((i) => i.id === sub);
      product.images = product.images.filter((i) => i.id !== sub).map((i, position) => ({ ...i, position }));
      if (removed?.is_primary && product.images[0]) product.images[0].is_primary = true;
      return jsonResponse(200, product);
    }
    return jsonResponse(404, { detail: "Not found" });
  }

  return {
    fetch,
    calls,
    products,
    /** Answer one method + path differently (return undefined to fall through). */
    override: (key: string, responder: (call: Call) => Response | undefined) => overrides.set(key, responder),
    last: (method: string, path: string) => [...calls].reverse().find((c) => c.method === method && c.path === path),
  };
}

/** A stand-in for XMLHttpRequest that reports progress, then succeeds (or fails with `status`). */
export class FakeXhr {
  static instances: FakeXhr[] = [];
  static status = 200;
  static delayMs = 10;
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: unknown;
  status = 0;
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor() {
    FakeXhr.instances.push(this);
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  send(body: unknown) {
    this.body = body;
    setTimeout(() => this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 }), 0);
    setTimeout(() => {
      this.status = FakeXhr.status;
      this.onload?.();
    }, FakeXhr.delayMs);
  }
}
