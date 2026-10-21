import { ApiError } from "@delea/api-client";
import { useState } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/toastContext";
import { useDeleteMaterial, type Material } from "./api";

/**
 * Delete with confirmation. A material that products use can't be deleted: the server's 409
 * ("…remove it from those products first.") is shown as sent.
 */
export function DeleteMaterialDialog({
  material,
  open,
  onOpenChange,
}: {
  material: Pick<Material, "id" | "name">;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const toast = useToast();
  const remove = useDeleteMaterial();
  const [error, setError] = useState<string | null>(null);

  const close = (next: boolean) => {
    if (!next) setError(null);
    onOpenChange(next);
  };

  const confirm = () => {
    setError(null);
    remove.mutate(material.id, {
      onSuccess: () => {
        toast({ title: "Material deleted", description: material.name, variant: "success" });
        close(false);
      },
      onError: (e) => setError(e instanceof ApiError ? e.message : "The material couldn't be deleted. Try again."),
    });
  };

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={close}
      title="Delete this material?"
      description={
        <p>
          <strong>{material.name}</strong> will be deleted. This can't be undone.
        </p>
      }
      confirmLabel="Delete material"
      busyLabel="Deleting…"
      danger
      busy={remove.isPending}
      onConfirm={confirm}
      error={error}
    />
  );
}
