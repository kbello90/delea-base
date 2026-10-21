import * as Dialog from "@radix-ui/react-dialog";
import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { Field } from "../components/Field";
import { useToast } from "../components/toastContext";
import { useCreateMaterial, useUpdateMaterial, type Material, type MaterialCreate, type MaterialUpdate } from "./api";
import {
  changedFields,
  EMPTY_FORM,
  toFormValues,
  validateMaterialForm,
  type MaterialFormErrors,
  type MaterialFormValues,
} from "./materialForm";

// The backend's message for a unique violation (errors.py, SQLSTATE 23505). On materials the
// only unique column a client sets is the name. The body has no error code, so match the text.
const DUPLICATE = "A record with these values already exists";

/**
 * Server errors: a duplicate name (409 with DUPLICATE) belongs on the name field; any other
 * error, including other 409s, goes on the form with the server's message.
 */
function applyServerError(e: unknown, setErrors: (errors: MaterialFormErrors) => void): string | null {
  if (e instanceof ApiError && e.status === 409 && e.message === DUPLICATE) {
    setErrors({ name: "Another material already has this name." });
    return null;
  }
  return e instanceof ApiError ? e.message : "The material couldn't be saved. Try again.";
}

/**
 * The create/edit form in a Radix dialog (focus trapped, Escape closes unless saving). `save`
 * gets a validated create body and closes the dialog itself on success.
 */
function MaterialFormDialog({
  open,
  onOpenChange,
  title,
  initial,
  submitLabel,
  busy,
  save,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  initial: MaterialFormValues;
  submitLabel: string;
  busy: boolean;
  save: (body: MaterialCreate, callbacks: { onError: (e: unknown) => void }) => void;
}) {
  const [values, setValues] = useState(initial);
  const [errors, setErrors] = useState<MaterialFormErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const set = (key: keyof MaterialFormValues, value: string) => setValues((v) => ({ ...v, [key]: value }));
  const input = (key: keyof MaterialFormValues, hasHint = false) => ({
    id: `material-${key}`,
    value: values[key],
    onChange: (e: { target: { value: string } }) => set(key, e.target.value),
    "aria-invalid": Boolean(errors[key]),
    "aria-describedby": errors[key] ? `material-${key}-error` : hasHint ? `material-${key}-hint` : undefined,
    disabled: busy,
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setServerError(null);
    const result = validateMaterialForm(values);
    setErrors(result.errors);
    if (!result.body) return;
    save(result.body, { onError: (e) => setServerError(applyServerError(e, setErrors)) });
  }

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog dialog--form" aria-describedby={undefined}>
          <Dialog.Title className="dialog__title">{title}</Dialog.Title>
          <form onSubmit={submit} noValidate>
            {serverError && (
              <p className="form-error" role="alert">
                {serverError}
              </p>
            )}
            <div className="form-grid">
              <Field id="material-name" label="Name" error={errors.name}>
                <input type="text" maxLength={200} {...input("name")} />
              </Field>
              <Field id="material-unit" label="Unit" error={errors.unit} hint="For example pcs, g or vials">
                <input type="text" maxLength={32} autoComplete="off" {...input("unit", true)} />
              </Field>
              <Field id="material-unitCost" label="Cost per unit" error={errors.unitCost}>
                <input type="text" inputMode="decimal" {...input("unitCost")} />
              </Field>
              <Field id="material-qtyOnHand" label="On hand" error={errors.qtyOnHand}>
                <input type="text" inputMode="decimal" {...input("qtyOnHand")} />
              </Field>
              <Field
                id="material-reorderThreshold"
                label="Reorder threshold"
                error={errors.reorderThreshold}
                hint="Flagged when on hand is below this"
              >
                <input type="text" inputMode="decimal" {...input("reorderThreshold", true)} />
              </Field>
            </div>
            <div className="dialog__actions">
              <Dialog.Close asChild>
                <Button type="button" variant="ghost" disabled={busy}>
                  Cancel
                </Button>
              </Dialog.Close>
              <Button type="submit" disabled={busy} aria-busy={busy}>
                {busy ? "Saving…" : submitLabel}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function NewMaterialDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const toast = useToast();
  const create = useCreateMaterial();
  return (
    <MaterialFormDialog
      open={open}
      onOpenChange={onOpenChange}
      title="New material"
      initial={EMPTY_FORM}
      submitLabel="Add material"
      busy={create.isPending}
      save={(body, { onError }) =>
        create.mutate(body, {
          onSuccess: (material) => {
            toast({ title: "Material added", description: material.name, variant: "success" });
            onOpenChange(false);
          },
          onError,
        })
      }
    />
  );
}

export function EditMaterialDialog({
  material,
  open,
  onOpenChange,
}: {
  material: Material;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const toast = useToast();
  const update = useUpdateMaterial(material.id);
  return (
    <MaterialFormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Edit ${material.name}`}
      initial={toFormValues(material)}
      submitLabel="Save changes"
      busy={update.isPending}
      save={(body, { onError }) => {
        const changes: MaterialUpdate = changedFields(material, body);
        if (Object.keys(changes).length === 0) {
          toast({ title: "No changes to save" });
          onOpenChange(false);
          return;
        }
        update.mutate(changes, {
          onSuccess: (saved) => {
            toast({ title: "Saved", description: saved.name, variant: "success" });
            onOpenChange(false);
          },
          onError,
        });
      }}
    />
  );
}
