import { ApiError } from "@delea/api-client";
import { useState } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/toastContext";
import { useDeleteExpense, type Expense } from "./api";

/** Delete with confirmation (owner only). The expense goes outright, with its receipt file. */
export function DeleteExpenseDialog({
  expense,
  open,
  onOpenChange,
  onDeleted,
}: {
  expense: Pick<Expense, "id" | "description" | "has_receipt">;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const toast = useToast();
  const remove = useDeleteExpense();
  const [error, setError] = useState<string | null>(null);

  const close = (next: boolean) => {
    if (!next) setError(null);
    onOpenChange(next);
  };

  const confirm = () => {
    setError(null);
    remove.mutate(expense.id, {
      onSuccess: () => {
        toast({ title: "Expense deleted", description: expense.description, variant: "success" });
        close(false);
        onDeleted?.();
      },
      onError: (e) => setError(e instanceof ApiError ? e.message : "The expense couldn't be deleted. Try again."),
    });
  };

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={close}
      title="Delete this expense?"
      description={
        <p>
          <strong>{expense.description}</strong>
          {expense.has_receipt ? " and its receipt will be deleted." : " will be deleted."} This can't be undone.
        </p>
      }
      confirmLabel="Delete expense"
      busyLabel="Deleting…"
      danger
      busy={remove.isPending}
      onConfirm={confirm}
      error={error}
    />
  );
}
