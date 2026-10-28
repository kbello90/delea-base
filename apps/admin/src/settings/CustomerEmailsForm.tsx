import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { Field } from "../components/Field";
import { useToast } from "../components/toastContext";
import {
  usePreviewEmail,
  useSendTestEmail,
  useUpdateEmailSettings,
  type CustomerEmails,
  type CustomerTemplate,
  type EmailPreview,
} from "./emailApi";

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
const INTRO_MAX = 300;

/** Each email and the trigger that sends it (production started and deposit received are D2's new triggers). */
const CUSTOMER_EMAILS: { key: CustomerTemplate; label: string; when: string }[] = [
  { key: "order_received", label: "Order received", when: "Sent when a shopper places an order: the W- reference, the items, the total, how to pay and the tracking link." },
  { key: "deposit_received", label: "Deposit received", when: "Sent when a deposit is received: the amount and what is left to pay." },
  { key: "in_production", label: "In production", when: "Sent when production starts, once per order." },
  { key: "payment_received", label: "Payment received", when: "Sent when the order is paid in full." },
  { key: "shipped", label: "Shipped", when: "Sent when you mark the order shipped, with the carrier and tracking number if you enter them." },
];

type Draft = Record<CustomerTemplate, { enabled: boolean; intro: string }>;

const toDraft = (emails: CustomerEmails): Draft =>
  Object.fromEntries(CUSTOMER_EMAILS.map(({ key }) => [key, { enabled: emails[key].enabled, intro: emails[key].intro ?? "" }])) as Draft;

const toBody = (draft: Draft): CustomerEmails =>
  Object.fromEntries(CUSTOMER_EMAILS.map(({ key }) => [key, { enabled: draft[key].enabled, intro: draft[key].intro.trim() || null }])) as CustomerEmails;

/** Settings › Emails › Emails to your customers (Slice D2): on or off, an intro, a preview, a test. */
export function CustomerEmailsForm({ emails }: { emails: CustomerEmails }) {
  const toast = useToast();
  const update = useUpdateEmailSettings();
  const preview = usePreviewEmail();
  const test = useSendTestEmail();
  const [draft, setDraft] = useState<Draft>(() => toDraft(emails));
  const [shown, setShown] = useState<{ key: CustomerTemplate; email: EmailPreview } | null>(null);
  const busy = update.isPending;

  const set = (key: CustomerTemplate, change: Partial<Draft[CustomerTemplate]>) => setDraft((d) => ({ ...d, [key]: { ...d[key], ...change } }));

  function save(event: FormEvent) {
    event.preventDefault();
    update.mutate({ customer: toBody(draft) }, { onSuccess: () => toast({ title: "Customer emails saved", variant: "success" }) });
  }

  function show(key: CustomerTemplate) {
    preview.mutate({ template: key, intro: draft[key].intro.trim() || null }, { onSuccess: (email) => setShown({ key, email }) });
  }

  const shownLabel = shown ? CUSTOMER_EMAILS.find((e) => e.key === shown.key)!.label : "";

  return (
    <>
      <form className="record-form emails-card__form" onSubmit={save} aria-labelledby="customer-emails-heading">
        <h3 id="customer-emails-heading">Emails to your customers</h3>
        <p className="muted">
          For website orders. They show the order reference, the items, the amounts, how to pay and the tracking link, never the shopper&apos;s
          address or phone number, and they advertise nothing.
        </p>
        {CUSTOMER_EMAILS.map(({ key, label, when }) => (
          <fieldset key={key} className="customer-email">
            <legend className="visually-hidden">{label}</legend>
            <label className="check emails-card__check">
              <input type="checkbox" checked={draft[key].enabled} onChange={(e) => set(key, { enabled: e.target.checked })} disabled={busy} />
              <span>
                {label}
                <span className="field-hint emails-card__hint">{when}</span>
              </span>
            </label>
            <Field id={`intro-${key}`} label={`Your intro for “${label}” (optional)`} hint={`Shown above the email's own text. Up to ${INTRO_MAX} characters.`}>
              <textarea
                id={`intro-${key}`}
                rows={2}
                maxLength={INTRO_MAX}
                value={draft[key].intro}
                onChange={(e) => set(key, { intro: e.target.value })}
                disabled={busy}
                aria-describedby={`intro-${key}-hint`}
              />
            </Field>
            <Button type="button" variant="ghost" onClick={() => show(key)} disabled={preview.isPending}>
              Preview “{label}”
            </Button>
          </fieldset>
        ))}
        {update.isError && (
          <p className="form-error" role="alert">
            {message(update.error)}
          </p>
        )}
        {preview.isError && (
          <p className="form-error" role="alert">
            {message(preview.error)}
          </p>
        )}
        <Button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save customer emails"}
        </Button>
      </form>

      {shown && (
        <section className="email-preview" aria-labelledby="email-preview-heading">
          <h3 id="email-preview-heading">Preview: {shownLabel}</h3>
          <p className="muted">A made-up order, at phone width. Nothing has been sent.</p>
          <p>
            <strong>Subject:</strong> {shown.email.subject}
          </p>
          {/* sandbox="": no scripts, no forms, no navigation; the HTML is escaped by the server as well */}
          <iframe className="email-preview__frame" title={`Preview of the “${shownLabel}” email`} sandbox="" srcDoc={shown.email.html} />
          <div className="emails-card__test">
            <Button
              type="button"
              variant="ghost"
              disabled={test.isPending}
              onClick={() =>
                test.mutate(shown.key, {
                  onSuccess: (out) =>
                    toast({
                      title: out.status === "suppressed" ? "Test not sent: today's limit is reached" : `“${shownLabel}” test on its way to ${out.sent_to}`,
                      variant: out.status === "suppressed" ? "error" : "success",
                    }),
                })
              }
            >
              {test.isPending ? "Sending…" : "Send this one to myself"}
            </Button>
            <span className="field-hint">With your saved intro, to the email you sign in with. Never to a customer.</span>
            {test.isError && (
              <p className="form-error" role="alert">
                {message(test.error)}
              </p>
            )}
          </div>
        </section>
      )}
    </>
  );
}
