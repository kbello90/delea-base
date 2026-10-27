import type { Schemas } from "@delea/api-client";
import type { Product } from "./api";

export type PersonalizationReplace = Schemas["PersonalizationReplace"];
export const MAX_FIELDS = 5;

/** One field as edited. Choices are typed one per line; ids are kept so carts still match. */
export interface FieldDraft {
  id: string | null;
  label: string;
  kind: "text" | "choice";
  required: boolean;
  maxLength: string;
  choices: string;
}

export type FieldErrors = Partial<Record<"label" | "maxLength" | "choices", string>>;

export const newField = (): FieldDraft => ({ id: null, label: "", kind: "text", required: false, maxLength: "40", choices: "" });

export function toDrafts(product: Product): FieldDraft[] {
  return product.personalization.map((f) => ({
    id: f.id,
    label: f.label,
    kind: f.kind,
    required: f.required,
    maxLength: f.max_length === null ? "40" : String(f.max_length),
    choices: (f.choices ?? []).join("\n"),
  }));
}

const choiceList = (text: string) =>
  text
    .split("\n")
    .map((c) => c.trim())
    .filter(Boolean);

/** The same rules as the API and the database: labels 1-40 and distinct, text 1-200, 1-30 distinct choices of 1-40. */
export function validateFields(drafts: FieldDraft[]): { errors: FieldErrors[]; body?: PersonalizationReplace } {
  const errors: FieldErrors[] = drafts.map(() => ({}));
  const seen = new Set<string>();
  drafts.forEach((d, i) => {
    const label = d.label.trim();
    if (!label) errors[i].label = "Enter a question, for example Ring size.";
    else if (label.length > 40) errors[i].label = "Use at most 40 characters.";
    else if (seen.has(label.toLowerCase())) errors[i].label = "Two fields can't have the same name.";
    seen.add(label.toLowerCase());
    if (d.kind === "text") {
      const n = Number(d.maxLength);
      if (!/^\d+$/.test(d.maxLength.trim()) || n < 1 || n > 200) errors[i].maxLength = "Use a number from 1 to 200.";
    } else {
      const choices = choiceList(d.choices);
      if (choices.length === 0) errors[i].choices = "Add at least one choice, one per line.";
      else if (choices.length > 30) errors[i].choices = "Use at most 30 choices.";
      else if (choices.some((c) => c.length > 40)) errors[i].choices = "Each choice can be at most 40 characters.";
      else if (new Set(choices.map((c) => c.toLowerCase())).size !== choices.length) errors[i].choices = "A choice is listed twice.";
    }
  });
  if (drafts.length > MAX_FIELDS || errors.some((e) => Object.keys(e).length > 0)) return { errors };
  return {
    errors,
    body: {
      fields: drafts.map((d) => ({
        ...(d.id ? { id: d.id } : {}),
        label: d.label.trim(),
        kind: d.kind,
        required: d.required,
        ...(d.kind === "text" ? { max_length: Number(d.maxLength) } : { choices: choiceList(d.choices) }),
      })),
    },
  };
}
