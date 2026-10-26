import type { Product } from "./api";

export type Field = Product["personalization"][number];

/**
 * The shopper's answers checked as the server checks them (storefront_price_cart): required
 * answered, choices from the list, text within its length and without control characters.
 * The server checks again; this only gives a clear message before the request.
 */
export function answerErrors(fields: Field[], answers: Record<string, string>): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const value = (answers[field.id] ?? "").trim();
    if (!value) {
      if (field.required) errors[field.id] = field.kind === "choice" ? `Choose a ${field.label.toLowerCase()}.` : `Enter ${field.label.toLowerCase()}.`;
      continue;
    }
    if (field.kind === "choice" && !(field.choices ?? []).includes(value)) {
      errors[field.id] = `Choose one of the listed options.`;
    } else if (field.kind === "text" && field.max_length !== null && value.length > field.max_length) {
      errors[field.id] = `Use at most ${field.max_length} characters.`;
    } else if (/\p{Cc}/u.test(value)) {
      errors[field.id] = "Use letters, numbers and punctuation only.";
    }
  }
  return errors;
}

/** Only this product's fields, trimmed, empty ones left out. */
export function cleanAnswers(fields: Field[], answers: Record<string, string>): Record<string, string> {
  const clean: Record<string, string> = {};
  for (const field of fields) {
    const value = (answers[field.id] ?? "").trim();
    if (value) clean[field.id] = value;
  }
  return clean;
}
