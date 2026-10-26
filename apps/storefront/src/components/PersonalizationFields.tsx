import type { Field } from "../lib/personalization";

/** The product's personalization questions, as labelled inputs (text with a counter, or a list). */
export function PersonalizationFields({
  fields,
  values,
  errors,
  onChange,
  disabled = false,
}: {
  fields: Field[];
  values: Record<string, string>;
  errors: Record<string, string>;
  onChange: (fieldId: string, value: string) => void;
  disabled?: boolean;
}) {
  if (fields.length === 0) return null;
  return (
    <fieldset className="personalize">
      <legend>Personalize it</legend>
      {fields.map((field) => {
        const id = `answer-${field.id}`;
        const value = values[field.id] ?? "";
        const error = errors[field.id];
        const describedBy = [error ? `${id}-error` : null, field.kind === "text" ? `${id}-count` : null].filter(Boolean).join(" ") || undefined;
        return (
          <div className="form-field" key={field.id}>
            <label htmlFor={id}>
              {field.label}
              {field.required ? <span className="required"> (required)</span> : <span className="optional"> (optional)</span>}
            </label>
            {field.kind === "choice" ? (
              <select id={id} value={value} onChange={(e) => onChange(field.id, e.target.value)} aria-invalid={Boolean(error)} aria-describedby={describedBy} aria-required={field.required} disabled={disabled}>
                <option value="">Choose…</option>
                {(field.choices ?? []).map((choice) => (
                  <option key={choice} value={choice}>
                    {choice}
                  </option>
                ))}
              </select>
            ) : (
              <>
                <input
                  id={id}
                  type="text"
                  value={value}
                  maxLength={field.max_length ?? 200}
                  onChange={(e) => onChange(field.id, e.target.value)}
                  aria-invalid={Boolean(error)}
                  aria-describedby={describedBy}
                  aria-required={field.required}
                  disabled={disabled}
                  autoComplete="off"
                />
                <span id={`${id}-count`} className="field-hint">
                  {value.length} / {field.max_length ?? 200}
                </span>
              </>
            )}
            {error && (
              <span id={`${id}-error`} className="field-error">
                {error}
              </span>
            )}
          </div>
        );
      })}
    </fieldset>
  );
}

/** Answers as shown in the bag, the checkout and the order: "Ring size: 7". */
export function Answers({ answers }: { answers: { label: string; value: string }[] | null | undefined }) {
  if (!answers || answers.length === 0) return null;
  return (
    <ul className="answers">
      {answers.map((answer) => (
        <li key={answer.label}>
          <span className="answers__label">{answer.label}:</span> {answer.value}
        </li>
      ))}
    </ul>
  );
}
