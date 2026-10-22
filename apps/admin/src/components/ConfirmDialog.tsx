import * as Dialog from "@radix-ui/react-dialog";
import { Button } from "@delea/ui";
import type { ReactNode } from "react";

/**
 * A confirmation dialog (Radix Dialog: focus trapped, Escape closes, focus returns to the
 * trigger). `error` shows inside the dialog, e.g. a 409 from the server, with an optional
 * extra action next to it.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  busyLabel = "Working…",
  onConfirm,
  busy = false,
  confirmDisabled = false,
  error = null,
  errorAction,
  danger = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  busyLabel?: string;
  onConfirm: () => void;
  busy?: boolean;
  /** Disables only the confirm button (e.g. until a required choice is made). */
  confirmDisabled?: boolean;
  error?: string | null;
  errorAction?: ReactNode;
  danger?: boolean;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog" aria-describedby="confirm-dialog-description">
          <Dialog.Title className="dialog__title">{title}</Dialog.Title>
          <Dialog.Description asChild>
            <div id="confirm-dialog-description" className="dialog__body">
              {description}
            </div>
          </Dialog.Description>
          {error && (
            <div className="dialog__error" role="alert">
              <p>{error}</p>
              {errorAction}
            </div>
          )}
          <div className="dialog__actions">
            <Dialog.Close asChild>
              <Button type="button" variant="ghost" disabled={busy}>
                Cancel
              </Button>
            </Dialog.Close>
            <Button
              type="button"
              className={danger ? "dl-button--danger" : undefined}
              disabled={busy || confirmDisabled}
              aria-busy={busy}
              onClick={onConfirm}
            >
              {busy ? busyLabel : confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
