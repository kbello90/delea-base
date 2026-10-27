import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { useToast } from "../components/toastContext";
import { useReplacePersonalization, type Product } from "./api";
import { MAX_FIELDS, newField, toDrafts, validateFields, type FieldDraft, type FieldErrors } from "./personalizationForm";

/**
 * Up to five questions shoppers answer for this product (Ring size, Initial…). No price or
 * stock per answer. Orders keep a copy of each answer, so editing here never changes them.
 */
export function PersonalizationEditor({ product }: { product: Product }) {
  const toast = useToast();
  const save = useReplacePersonalization(product.id);
  const [drafts, setDrafts] = useState<FieldDraft[]>(() => toDrafts(product));
  const [errors, setErrors] = useState<FieldErrors[]>([]);
  const busy = save.isPending;

  const update = (i: number, change: Partial<FieldDraft>) => setDrafts((all) => all.map((d, j) => (j === i ? { ...d, ...change } : d)));
  const move = (i: number, by: -1 | 1) =>
    setDrafts((all) => {
      const next = [...all];
      [next[i], next[i + by]] = [next[i + by], next[i]];
      return next;
    });

  function submit(event: FormEvent) {
    event.preventDefault();
    const { errors: found, body } = validateFields(drafts);
    setErrors(found);
    if (!body) return;
    save.mutate(body, {
      onSuccess: (saved) => {
        setDrafts(toDrafts(saved));
        toast({ title: "Personalization saved", variant: "success" });
      },
    });
  }

  return (
    <form className="personalization-editor" onSubmit={submit} noValidate aria-label="Personalization">
      <p className="muted">Questions shoppers answer when they order this piece, for example ring size or an initial. At most {MAX_FIELDS}.</p>
      {save.isError && (
        <p className="form-error" role="alert">
          {save.error instanceof ApiError ? save.error.message : "The questions couldn't be saved. Try again."}
        </p>
      )}
      {drafts.length === 0 && <p className="muted">No questions yet.</p>}
      <ol className="pf-list">
        {drafts.map((d, i) => {
          const e = errors[i] ?? {};
          const id = `pf-${i}`;
          return (
            <li key={d.id ?? `new-${i}`} className="pf-item">
              <fieldset disabled={busy}>
                <legend>Question {i + 1}</legend>
                <div className="form-grid">
                  <div className="field">
                    <label htmlFor={`${id}-label`}>Question</label>
                    <input id={`${id}-label`} type="text" maxLength={40} value={d.label} onChange={(ev) => update(i, { label: ev.target.value })} aria-invalid={Boolean(e.label)} aria-describedby={e.label ? `${id}-label-error` : undefined} />
                    {e.label && (
                      <span id={`${id}-label-error`} className="field-error">
                        {e.label}
                      </span>
                    )}
                  </div>
                  <div className="field">
                    <label htmlFor={`${id}-kind`}>Answer</label>
                    <select id={`${id}-kind`} value={d.kind} onChange={(ev) => update(i, { kind: ev.target.value as FieldDraft["kind"] })}>
                      <option value="text">Free text</option>
                      <option value="choice">A list of choices</option>
                    </select>
                  </div>
                </div>
                {d.kind === "text" ? (
                  <div className="field">
                    <label htmlFor={`${id}-max`}>Longest answer (characters)</label>
                    <input id={`${id}-max`} type="text" inputMode="numeric" value={d.maxLength} onChange={(ev) => update(i, { maxLength: ev.target.value })} aria-invalid={Boolean(e.maxLength)} aria-describedby={e.maxLength ? `${id}-max-error` : undefined} />
                    {e.maxLength && (
                      <span id={`${id}-max-error`} className="field-error">
                        {e.maxLength}
                      </span>
                    )}
                  </div>
                ) : (
                  <div className="field">
                    <label htmlFor={`${id}-choices`}>Choices, one per line</label>
                    <textarea id={`${id}-choices`} rows={4} value={d.choices} onChange={(ev) => update(i, { choices: ev.target.value })} aria-invalid={Boolean(e.choices)} aria-describedby={e.choices ? `${id}-choices-error` : undefined} />
                    {e.choices && (
                      <span id={`${id}-choices-error`} className="field-error">
                        {e.choices}
                      </span>
                    )}
                  </div>
                )}
                <label className="check">
                  <input type="checkbox" checked={d.required} onChange={(ev) => update(i, { required: ev.target.checked })} />
                  Required (shoppers must answer before adding it to the bag)
                </label>
                <div className="pf-item__actions">
                  <button type="button" className="btn-text" onClick={() => move(i, -1)} disabled={i === 0} aria-label={`Move question ${i + 1} up`}>
                    Move up
                  </button>
                  <button type="button" className="btn-text" onClick={() => move(i, 1)} disabled={i === drafts.length - 1} aria-label={`Move question ${i + 1} down`}>
                    Move down
                  </button>
                  <button type="button" className="btn-text btn-text--danger" onClick={() => setDrafts((all) => all.filter((_, j) => j !== i))} aria-label={`Remove question ${i + 1}`}>
                    Remove
                  </button>
                </div>
              </fieldset>
            </li>
          );
        })}
      </ol>
      <div className="form-actions">
        <button type="button" className="btn-text" onClick={() => setDrafts((all) => [...all, newField()])} disabled={busy || drafts.length >= MAX_FIELDS}>
          Add a question
        </button>
        {drafts.length >= MAX_FIELDS && <span className="muted">That's the most a product can have.</span>}
        <Button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save personalization"}
        </Button>
      </div>
    </form>
  );
}

/** Read-only, for staff. */
export function PersonalizationList({ product }: { product: Product }) {
  if (product.personalization.length === 0) return <p className="muted">No personalization questions.</p>;
  return (
    <ul className="pf-readonly">
      {product.personalization.map((f) => (
        <li key={f.id}>
          <strong>{f.label}</strong>
          {f.required ? " (required)" : " (optional)"}: {f.kind === "choice" ? (f.choices ?? []).join(", ") : `free text, up to ${f.max_length} characters`}
        </li>
      ))}
    </ul>
  );
}
