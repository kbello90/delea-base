import { centsToInput, parseMoneyInput } from "../lib/moneyInput";
import { RECEIPT_MAX_BYTES, RECEIPT_TYPES, type Expense, type ExpenseCategory, type ExpenseCreate, type ReceiptType } from "./api";

export interface ExpenseFormValues {
  date: string; // YYYY-MM-DD
  description: string;
  category: ExpenseCategory;
  amount: string;
  deductible: boolean;
}

export type ExpenseFormErrors = Partial<Record<keyof ExpenseFormValues, string>>;

export const EMPTY_FORM: ExpenseFormValues = {
  date: "",
  description: "",
  category: "other",
  amount: "",
  deductible: true,
};

const DAY = /^\d{4}-\d{2}-\d{2}$/;

export function toFormValues(e: Expense): ExpenseFormValues {
  return {
    date: e.expense_date,
    description: e.description,
    category: e.category,
    amount: centsToInput(e.amount_cents),
    deductible: e.is_tax_deductible,
  };
}

/** The body to send, or the errors to show. The API checks everything again. */
export function validateExpenseForm(v: ExpenseFormValues): { body?: ExpenseCreate; errors: ExpenseFormErrors } {
  const errors: ExpenseFormErrors = {};
  if (!DAY.test(v.date)) errors.date = "Enter the date of the expense.";
  const description = v.description.trim();
  if (!description) errors.description = "Describe the expense.";
  else if (description.length > 500) errors.description = "Use at most 500 characters.";
  const amount = parseMoneyInput(v.amount);
  if (!amount.ok) errors.amount = amount.error;
  if (Object.keys(errors).length > 0 || !amount.ok) return { errors };
  return {
    errors,
    body: {
      expense_date: v.date,
      description,
      category: v.category,
      amount_cents: amount.cents,
      is_tax_deductible: v.deductible,
    },
  };
}

/** Only the fields that differ from the saved expense (a PATCH body). */
export function changedFields(saved: Expense, body: ExpenseCreate): Partial<ExpenseCreate> {
  const changes: Partial<ExpenseCreate> = {};
  if (body.expense_date !== saved.expense_date) changes.expense_date = body.expense_date;
  if (body.description !== saved.description) changes.description = body.description;
  if (body.category !== saved.category) changes.category = body.category;
  if (body.amount_cents !== saved.amount_cents) changes.amount_cents = body.amount_cents;
  if (body.is_tax_deductible !== saved.is_tax_deductible) changes.is_tax_deductible = body.is_tax_deductible;
  return changes;
}

/** A receipt file's type, or why it can't be uploaded (checked again, by content, on the server). */
export function checkReceiptFile(file: File): { type: ReceiptType } | { error: string } {
  const type = RECEIPT_TYPES.find((t) => t.value === file.type)?.value;
  if (!type) return { error: `${file.name} can't be used: choose a JPEG, PNG or WebP photo, or a PDF.` };
  if (file.size === 0) return { error: `${file.name} is empty.` };
  if (file.size > RECEIPT_MAX_BYTES) return { error: `${file.name} is larger than 10 MB.` };
  return { type };
}

/** "2.4 MB", "830 KB". */
export function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

const dateFormat = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeZone: "UTC" });

/** A plain date ("2026-10-23", no time zone) as "Oct 23, 2026". */
export const formatDay = (day: string) => dateFormat.format(new Date(`${day}T00:00:00Z`));
