import { describe, expect, it } from "vitest";
import { changedFields, checkReceiptFile, EMPTY_FORM, toFormValues, validateExpenseForm } from "./expenseForm";
import { makeExpense } from "./testServer";

const file = (name: string, type: string, bytes = 10) => new File([new Uint8Array(bytes)], name, { type });

describe("validateExpenseForm", () => {
  it("builds the body from valid values", () => {
    const { body, errors } = validateExpenseForm({ date: "2026-10-01", description: "  Rent  ", category: "rent", amount: "1,200.5", deductible: true });
    expect(errors).toEqual({});
    expect(body).toEqual({ expense_date: "2026-10-01", description: "Rent", category: "rent", amount_cents: 120050, is_tax_deductible: true });
  });

  it("reports every missing or invalid field", () => {
    expect(validateExpenseForm(EMPTY_FORM).errors).toEqual({
      date: "Enter the date of the expense.",
      description: "Describe the expense.",
      amount: "Enter an amount.",
    });
    expect(validateExpenseForm({ ...EMPTY_FORM, date: "2026-10-01", description: "x".repeat(501), amount: "-5" }).errors).toEqual({
      description: "Use at most 500 characters.",
      amount: "The amount can't be negative.",
    });
  });
});

describe("changedFields", () => {
  it("returns only what differs", () => {
    const saved = makeExpense({ amount_cents: 1000, category: "meals" });
    const { body } = validateExpenseForm({ ...toFormValues(saved), amount: "10.01" });
    expect(changedFields(saved, body!)).toEqual({ amount_cents: 1001 });
  });
});

describe("checkReceiptFile", () => {
  it("accepts JPEG, PNG, WebP and PDF up to 10 MB", () => {
    for (const type of ["image/jpeg", "image/png", "image/webp", "application/pdf"]) {
      expect(checkReceiptFile(file("r", type))).toEqual({ type });
    }
    expect(checkReceiptFile(file("r.pdf", "application/pdf", 10 * 1024 * 1024))).toEqual({ type: "application/pdf" });
  });

  it("refuses other types, empty files and files over 10 MB", () => {
    expect(checkReceiptFile(file("r.gif", "image/gif"))).toEqual({ error: "r.gif can't be used: choose a JPEG, PNG or WebP photo, or a PDF." });
    expect(checkReceiptFile(file("r.html", "text/html"))).toHaveProperty("error");
    expect(checkReceiptFile(file("r.pdf", "application/pdf", 0))).toEqual({ error: "r.pdf is empty." });
    expect(checkReceiptFile(file("r.pdf", "application/pdf", 10 * 1024 * 1024 + 1))).toEqual({ error: "r.pdf is larger than 10 MB." });
  });
});
