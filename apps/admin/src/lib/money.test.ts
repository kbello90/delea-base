import { describe, expect, it } from "vitest";
import { formatMoney } from "./money";

describe("formatMoney", () => {
  it.each([
    [1999, "$19.99"],
    [100, "$1.00"],
    [1, "$0.01"],
    [5, "$0.05"],
    [10, "$0.10"],
    [0, "$0.00"],
    [123456789, "$1,234,567.89"],
  ])("exact cents: %i -> %s", (cents, text) => {
    expect(formatMoney(cents, "USD")).toBe(text);
  });

  it.each([
    [995, "$9.95"],
    [999, "$9.99"],
    [99, "$0.99"],
    [1005, "$10.05"],
  ])("never rounds the cents it is given: %i -> %s", (cents, text) => {
    expect(formatMoney(cents, "USD")).toBe(text);
  });

  it.each([
    [-81500, "-$815.00"],
    [-5, "-$0.05"],
    [-99, "-$0.99"],
  ])("negative amounts: %i -> %s", (cents, text) => {
    expect(formatMoney(cents, "USD")).toBe(text);
  });

  it("large values stay exact (where cents / 100 in floating point would not)", () => {
    expect(formatMoney(Number.MAX_SAFE_INTEGER, "USD")).toBe("$90,071,992,547,409.91");
    expect(formatMoney(-Number.MAX_SAFE_INTEGER, "USD")).toBe("-$90,071,992,547,409.91");
    expect(formatMoney(100_000_000_000, "USD")).toBe("$1,000,000,000.00");
  });

  it.each([
    [123456, "EUR", "en-US", "€1,234.56"],
    [123456, "EUR", "de-DE", "1.234,56 €"],
    [123456, "GBP", "en-GB", "£1,234.56"],
    [5, "MXN", "es-MX", "$0.05"],
    [123456, "CAD", "en-US", "CA$1,234.56"],
  ])("non-USD currencies and locales: %i %s %s", (cents, currency, locale, text) => {
    expect(formatMoney(cents, currency, locale)).toBe(text);
  });

  it.each([0.5, 19.99, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1])(
    "rejects anything but safe integer cents: %s",
    (value) => {
      expect(() => formatMoney(value, "USD")).toThrow(RangeError);
    },
  );
});
