import { centsToInput, parseMoneyInput } from "../lib/moneyInput";
import type { Material, MaterialCreate, MaterialUpdate } from "./api";

export interface MaterialFormValues {
  name: string;
  unit: string;
  unitCost: string;
  qtyOnHand: string;
  reorderThreshold: string;
}

export type MaterialFormErrors = Partial<Record<keyof MaterialFormValues, string>>;

export const EMPTY_FORM: MaterialFormValues = {
  name: "",
  unit: "",
  unitCost: "",
  qtyOnHand: "0",
  reorderThreshold: "0",
};

// numeric(12,2): up to 10 whole digits and 2 decimals, never negative.
const QUANTITY = /^\d{1,10}(?:\.\d{1,2})?$/;

/**
 * A quantity as the exact two-decimal string the API returns ("3" -> "3.00"), so it is sent and
 * compared without floating point.
 */
function parseQuantity(raw: string, label: string): { value?: string; error?: string } {
  const text = raw.trim();
  if (!text) return { error: `Enter ${label.toLowerCase()}.` };
  if (text.startsWith("-")) return { error: `${label} can't be negative.` };
  if (/\.\d{3,}$/.test(text)) return { error: "Use at most 2 decimal places." };
  if (!QUANTITY.test(text)) return { error: `${label} must be a number like 12 or 2.5.` };
  const [whole, fraction = ""] = text.split(".");
  return { value: `${BigInt(whole)}.${fraction.padEnd(2, "0")}` };
}

/** A quantity for display: "18.00" -> "18", "2.50" -> "2.5". */
export function formatQuantity(qty: string): string {
  return qty.includes(".") ? qty.replace(/\.?0+$/, "") : qty;
}

export function toFormValues(m: Material): MaterialFormValues {
  return {
    name: m.name,
    unit: m.unit,
    unitCost: centsToInput(m.unit_cost_cents),
    qtyOnHand: formatQuantity(m.qty_on_hand),
    reorderThreshold: formatQuantity(m.reorder_threshold),
  };
}

/** Validates the form and builds the create body (cost as integer cents), or returns errors. */
export function validateMaterialForm(v: MaterialFormValues): { errors: MaterialFormErrors; body?: MaterialCreate } {
  const errors: MaterialFormErrors = {};
  const name = v.name.trim();
  const unit = v.unit.trim();
  if (!name) errors.name = "Enter a material name.";
  else if (name.length > 200) errors.name = "Use at most 200 characters.";
  if (!unit) errors.unit = "Enter a unit, for example pcs, g or vials.";
  else if (unit.length > 32) errors.unit = "Use at most 32 characters.";

  const cost = parseMoneyInput(v.unitCost);
  if (!cost.ok) errors.unitCost = cost.error;
  const qty = parseQuantity(v.qtyOnHand, "Quantity on hand");
  if (qty.error) errors.qtyOnHand = qty.error;
  const threshold = parseQuantity(v.reorderThreshold, "The reorder threshold");
  if (threshold.error) errors.reorderThreshold = threshold.error;

  if (Object.keys(errors).length > 0 || !cost.ok || !qty.value || !threshold.value) return { errors };
  return {
    errors,
    body: {
      name,
      unit,
      unit_cost_cents: cost.cents,
      qty_on_hand: qty.value,
      reorder_threshold: threshold.value,
    },
  };
}

/** Only the fields that changed, for PATCH. */
export function changedFields(material: Material, body: MaterialCreate): MaterialUpdate {
  const changes: MaterialUpdate = {};
  if (body.name !== material.name) changes.name = body.name;
  if (body.unit !== material.unit) changes.unit = body.unit;
  if (body.unit_cost_cents !== material.unit_cost_cents) changes.unit_cost_cents = body.unit_cost_cents;
  if (body.qty_on_hand !== material.qty_on_hand) changes.qty_on_hand = body.qty_on_hand;
  if (body.reorder_threshold !== material.reorder_threshold) changes.reorder_threshold = body.reorder_threshold;
  return changes;
}
