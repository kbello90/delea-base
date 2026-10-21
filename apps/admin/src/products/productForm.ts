import { centsToInput, parseMoneyInput } from "../lib/moneyInput";
import type { Metal, Product, ProductCreate, ProductUpdate } from "./api";

export interface ProductFormValues {
  name: string;
  sku: string;
  description: string;
  metal: Metal | "";
  price: string;
  salePrice: string;
  saleEndsAt: string; // <input type="datetime-local"> value, the user's local time
  stockQty: string;
  lowStockThreshold: string;
  isPublished: boolean;
}

export type ProductFormErrors = Partial<Record<keyof ProductFormValues, string>>;

export const EMPTY_FORM: ProductFormValues = {
  name: "",
  sku: "",
  description: "",
  metal: "",
  price: "",
  salePrice: "",
  saleEndsAt: "",
  stockQty: "0",
  lowStockThreshold: "3",
  isPublished: false,
};

const INT32_MAX = 2_147_483_647;

function wholeNumber(raw: string, label: string): { value?: number; error?: string } {
  const text = raw.trim();
  if (!/^\d+$/.test(text)) return { error: `${label} must be a whole number of 0 or more.` };
  const value = Number(text);
  if (value > INT32_MAX) return { error: `${label} is too large.` };
  return { value };
}

function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function toFormValues(p: Product): ProductFormValues {
  return {
    name: p.name,
    sku: p.sku,
    description: p.description ?? "",
    metal: p.metal,
    price: centsToInput(p.price_cents),
    salePrice: p.sale_price_cents === null ? "" : centsToInput(p.sale_price_cents),
    saleEndsAt: p.sale_ends_at ? toLocalInput(p.sale_ends_at) : "",
    stockQty: String(p.stock_qty),
    lowStockThreshold: String(p.low_stock_threshold),
    isPublished: p.is_published,
  };
}

/** Validates the form and builds the create body (money as integer cents), or returns errors. */
export function validateProductForm(v: ProductFormValues): { errors: ProductFormErrors; body?: ProductCreate } {
  const errors: ProductFormErrors = {};
  const name = v.name.trim();
  const sku = v.sku.trim();
  if (!name) errors.name = "Enter a product name.";
  else if (name.length > 200) errors.name = "Use at most 200 characters.";
  if (!sku) errors.sku = "Enter a SKU.";
  else if (sku.length > 64) errors.sku = "Use at most 64 characters.";
  if (!v.metal) errors.metal = "Choose a metal.";

  const price = parseMoneyInput(v.price);
  if (!price.ok) errors.price = price.error;

  let salePrice: number | null = null;
  if (v.salePrice.trim()) {
    const parsed = parseMoneyInput(v.salePrice);
    if (!parsed.ok) errors.salePrice = parsed.error;
    else if (price.ok && parsed.cents >= price.cents) errors.salePrice = "The sale price must be lower than the price.";
    else salePrice = parsed.cents;
  }

  let saleEndsAt: string | null = null;
  if (v.saleEndsAt) {
    const when = new Date(v.saleEndsAt);
    if (Number.isNaN(when.getTime())) errors.saleEndsAt = "Enter a valid date and time.";
    else if (!v.salePrice.trim()) errors.saleEndsAt = "Set a sale price, or clear the end date.";
    else saleEndsAt = when.toISOString();
  }

  const stock = wholeNumber(v.stockQty, "Stock");
  if (stock.error) errors.stockQty = stock.error;
  const threshold = wholeNumber(v.lowStockThreshold, "The low-stock threshold");
  if (threshold.error) errors.lowStockThreshold = threshold.error;

  if (Object.keys(errors).length > 0 || !price.ok || !v.metal) return { errors };
  return {
    errors,
    body: {
      name,
      sku,
      description: v.description.trim() || null,
      metal: v.metal,
      price_cents: price.cents,
      sale_price_cents: salePrice,
      sale_ends_at: saleEndsAt,
      stock_qty: stock.value ?? 0,
      low_stock_threshold: threshold.value ?? 3,
      is_published: v.isPublished,
      sort_order: 0, // storefront order is edited elsewhere; a PATCH never sends it
    },
  };
}

/** Only the fields that changed, for PATCH. */
export function changedFields(product: Product, body: ProductCreate): ProductUpdate {
  const changes: ProductUpdate = {};
  const current: Record<string, unknown> = {
    name: product.name,
    sku: product.sku,
    description: product.description,
    metal: product.metal,
    price_cents: product.price_cents,
    sale_price_cents: product.sale_price_cents,
    sale_ends_at: product.sale_ends_at ? new Date(product.sale_ends_at).toISOString() : null,
    stock_qty: product.stock_qty,
    low_stock_threshold: product.low_stock_threshold,
    is_published: product.is_published,
  };
  const fields: Partial<ProductCreate> = { ...body };
  delete fields.sort_order; // never sent on edit
  for (const [key, value] of Object.entries(fields) as [keyof ProductCreate, unknown][]) {
    if (key in current && current[key] !== value) (changes as Record<string, unknown>)[key] = value;
  }
  return changes;
}
