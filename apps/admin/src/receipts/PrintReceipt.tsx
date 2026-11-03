import * as Dialog from "@radix-ui/react-dialog";
import { Button } from "@delea/ui";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { Invoice } from "../invoices/api";
import { useCurrency } from "../lib/useCurrency";
import { isThermal, PAGE_RULES, RECEIPT_LAYOUTS, useReceiptSettings, type ReceiptLayout } from "./api";
import { ReceiptDocument } from "./ReceiptDocument";
import { isDepositReceipt } from "./receiptFormat";
import "./receipt.css";

export const THERMAL_NOTE = "Thermal printer: in the print dialog choose Margins: None and Scale: 100%.";

/**
 * "Print receipt": a dialog to pick the paper for this receipt (the shop's default is chosen
 * first) with a preview, then the browser's print dialog. While printing, only the receipt is
 * on the page, with the paper's @page rule.
 */
export function PrintReceiptButton({ invoice, variant = "ghost" }: { invoice: Invoice; variant?: "ghost" | "primary" }) {
  const settings = useReceiptSettings();
  const currency = useCurrency();
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<ReceiptLayout | null>(null);
  const [printing, setPrinting] = useState<ReceiptLayout | null>(null);
  const layout = choice ?? settings.data?.receipt_layout ?? "letter";
  const ready = settings.isSuccess && currency.status === "success";
  const label = isDepositReceipt(invoice) ? "Print deposit receipt" : "Print receipt";

  useEffect(() => {
    if (!printing) return;
    const done = () => setPrinting(null);
    document.body.classList.add("printing-receipt");
    window.addEventListener("afterprint", done, { once: true });
    // After the receipt is on the page; Chrome returns from print() when its dialog closes.
    const timer = window.setTimeout(() => window.print(), 0);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("afterprint", done);
      document.body.classList.remove("printing-receipt");
    };
  }, [printing]);

  function print() {
    setOpen(false);
    setPrinting(layout);
  }

  return (
    <>
      <Button type="button" variant={variant} onClick={() => setOpen(true)} disabled={!ready}>
        {label}
      </Button>
      <Dialog.Root
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setChoice(null); // the next receipt starts from the shop's default again
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog receipt-dialog" aria-describedby={undefined}>
            <Dialog.Title className="dialog__title">{label}</Dialog.Title>
            {ready && settings.data && currency.status === "success" && (
              <div className="dialog__body">
                <fieldset className="receipt-paper">
                  <legend>Paper</legend>
                  {RECEIPT_LAYOUTS.map((l) => (
                    <label key={l.value} className="check">
                      <input type="radio" name="receipt-paper" value={l.value} checked={layout === l.value} onChange={() => setChoice(l.value)} />
                      <span>
                        {l.label}
                        <span className="muted">
                          {" "}
                          · {l.hint}
                          {settings.data.receipt_layout === l.value && " · your default"}
                        </span>
                      </span>
                    </label>
                  ))}
                </fieldset>
                {isThermal(layout) && (
                  <p className="field-hint" role="note">
                    {THERMAL_NOTE}
                  </p>
                )}
                <div className="receipt-preview" role="region" aria-label="Receipt preview">
                  <ReceiptDocument invoice={invoice} settings={settings.data} layout={layout} currency={currency.currency} />
                </div>
              </div>
            )}
            <div className="dialog__actions">
              <Dialog.Close asChild>
                <Button type="button" variant="ghost">
                  Cancel
                </Button>
              </Dialog.Close>
              <Button type="button" onClick={print} disabled={!ready}>
                Print
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      {printing &&
        settings.data &&
        currency.status === "success" &&
        createPortal(
          <div className="receipt-print-root" data-layout={printing}>
            <style>{PAGE_RULES[printing]}</style>
            <ReceiptDocument invoice={invoice} settings={settings.data} layout={printing} currency={currency.currency} />
          </div>,
          document.body,
        )}
    </>
  );
}
