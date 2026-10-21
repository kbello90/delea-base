import { describe, expect, it } from "vitest";
import { changedFields, EMPTY_FORM, formatQuantity, toFormValues, validateMaterialForm } from "./materialForm";
import { makeMaterial } from "./testServer";

describe("validateMaterialForm", () => {
  const valid = { ...EMPTY_FORM, name: " Silver wire ", unit: " g ", unitCost: "1,250.5" };

  it("builds the body with integer cents and exact two-decimal quantities", () => {
    const { errors, body } = validateMaterialForm({ ...valid, qtyOnHand: "3", reorderThreshold: "0.5" });
    expect(errors).toEqual({});
    expect(body).toEqual({
      name: "Silver wire",
      unit: "g",
      unit_cost_cents: 125050,
      qty_on_hand: "3.00",
      reorder_threshold: "0.50",
    });
  });

  it("keeps large quantities exact", () => {
    expect(validateMaterialForm({ ...valid, qtyOnHand: "9999999999.99" }).body?.qty_on_hand).toBe("9999999999.99");
    expect(validateMaterialForm({ ...valid, qtyOnHand: "007" }).body?.qty_on_hand).toBe("7.00");
  });

  it.each([
    [{ name: "" }, "name", "Enter a material name."],
    [{ name: "x".repeat(201) }, "name", "Use at most 200 characters."],
    [{ unit: " " }, "unit", "Enter a unit, for example pcs, g or vials."],
    [{ unit: "x".repeat(33) }, "unit", "Use at most 32 characters."],
    [{ unitCost: "" }, "unitCost", "Enter an amount."],
    [{ unitCost: "1.234" }, "unitCost", "Use at most 2 decimal places."],
    [{ qtyOnHand: "" }, "qtyOnHand", "Enter quantity on hand."],
    [{ qtyOnHand: "-1" }, "qtyOnHand", "Quantity on hand can't be negative."],
    [{ qtyOnHand: "2.505" }, "qtyOnHand", "Use at most 2 decimal places."],
    [{ qtyOnHand: "12345678901" }, "qtyOnHand", "Quantity on hand must be a number like 12 or 2.5."],
    [{ reorderThreshold: "abc" }, "reorderThreshold", "The reorder threshold must be a number like 12 or 2.5."],
  ])("%o -> %s: %s", (change, field, message) => {
    const { errors, body } = validateMaterialForm({ ...valid, ...change });
    expect(errors[field as keyof typeof errors]).toBe(message);
    expect(body).toBeUndefined();
  });
});

describe("formatQuantity", () => {
  it.each([
    ["18.00", "18"],
    ["2.50", "2.5"],
    ["0.05", "0.05"],
    ["100.00", "100"],
    ["0.00", "0"],
    ["40", "40"],
  ])("%s -> %s", (qty, shown) => expect(formatQuantity(qty)).toBe(shown));
});

describe("editing", () => {
  const material = makeMaterial({ name: "Box", unit: "pcs", unit_cost_cents: 150, qty_on_hand: "12.00", reorder_threshold: "2.50" });

  it("round-trips the material through the form", () => {
    const values = toFormValues(material);
    expect(values).toEqual({ name: "Box", unit: "pcs", unitCost: "1.50", qtyOnHand: "12", reorderThreshold: "2.5" });
    const { body } = validateMaterialForm(values);
    expect(changedFields(material, body!)).toEqual({});
  });

  it("sends only what changed", () => {
    const { body } = validateMaterialForm({ ...toFormValues(material), qtyOnHand: "4", unitCost: "2" });
    expect(changedFields(material, body!)).toEqual({ qty_on_hand: "4.00", unit_cost_cents: 200 });
  });
});
