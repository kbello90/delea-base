import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/toastContext";
import { useDeleteProduct, useUpdateProduct, type Product } from "./api";

/**
 * Delete with confirmation. A product on any invoice can't be deleted: the server's 409
 * ("…Unpublish it instead…") is shown as sent, with an "Unpublish instead" action when the
 * product is still published.
 */
export function DeleteProductDialog({
  product,
  open,
  onOpenChange,
  onDeleted,
}: {
  product: Pick<Product, "id" | "name" | "is_published">;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const toast = useToast();
  const remove = useDeleteProduct();
  const unpublish = useUpdateProduct(product.id);
  const [error, setError] = useState<string | null>(null);

  const close = (next: boolean) => {
    if (!next) setError(null);
    onOpenChange(next);
  };

  const confirm = () => {
    setError(null);
    remove.mutate(product.id, {
      onSuccess: () => {
        toast({ title: "Product deleted", description: product.name, variant: "success" });
        close(false);
        onDeleted?.();
      },
      onError: (e) => setError(e instanceof ApiError ? e.message : "The product couldn't be deleted. Try again."),
    });
  };

  const unpublishInstead = () =>
    unpublish.mutate(
      { is_published: false },
      {
        onSuccess: () => {
          toast({ title: "Product unpublished", description: `${product.name} is hidden from the storefront.` });
          close(false);
        },
        onError: (e) => setError(e instanceof ApiError ? e.message : "The product couldn't be unpublished."),
      },
    );

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={close}
      title="Delete this product?"
      description={
        <p>
          <strong>{product.name}</strong> and its images will be deleted. This can't be undone.
        </p>
      }
      confirmLabel="Delete product"
      busyLabel="Deleting…"
      danger
      busy={remove.isPending || unpublish.isPending}
      onConfirm={confirm}
      error={error}
      errorAction={
        error && product.is_published ? (
          <Button type="button" variant="ghost" onClick={unpublishInstead} disabled={unpublish.isPending}>
            {unpublish.isPending ? "Unpublishing…" : "Unpublish instead"}
          </Button>
        ) : null
      }
    />
  );
}
