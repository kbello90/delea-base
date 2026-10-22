import { ApiError } from "@delea/api-client";
import { useState } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/toastContext";
import { customerName, useDeleteCustomer, type Customer } from "./api";

/**
 * Delete with confirmation (owner only). A customer with invoices can't be deleted: the
 * server's 409 ("…so they can't be deleted…") is shown as sent. The toast names the customer
 * only; contact details never go in a toast.
 */
export function DeleteCustomerDialog({
  customer,
  open,
  onOpenChange,
  onDeleted,
}: {
  customer: Pick<Customer, "id" | "customer_type" | "company_name" | "first_name" | "last_name">;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const toast = useToast();
  const remove = useDeleteCustomer();
  const [error, setError] = useState<string | null>(null);
  const name = customerName(customer);

  const close = (next: boolean) => {
    if (!next) setError(null);
    onOpenChange(next);
  };

  const confirm = () => {
    setError(null);
    remove.mutate(customer.id, {
      onSuccess: () => {
        toast({ title: "Customer deleted", description: name, variant: "success" });
        close(false);
        onDeleted?.();
      },
      onError: (e) => setError(e instanceof ApiError ? e.message : "The customer couldn't be deleted. Try again."),
    });
  };

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={close}
      title="Delete this customer?"
      description={
        <p>
          <strong>{name}</strong> and their contact details will be deleted. This can't be undone.
        </p>
      }
      confirmLabel="Delete customer"
      busyLabel="Deleting…"
      danger
      busy={remove.isPending}
      onConfirm={confirm}
      error={error}
    />
  );
}
