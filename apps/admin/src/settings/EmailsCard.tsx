import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { Field } from "../components/Field";
import { useToast } from "../components/toastContext";
import { CustomerEmailsForm } from "./CustomerEmailsForm";
import { useRequestReplyTo, useSendTestEmail, useUpdateEmailSettings, type EmailSettings, type OwnerAlerts } from "./emailApi";

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

const PROVIDER: Record<EmailSettings["provider"], string> = {
  resend: "Sending",
  local: "Local mail catcher (development)",
  off: "Not set up on this server: emails wait until it is",
};

const ALERTS: { key: keyof OwnerAlerts; label: string; hint: string }[] = [
  { key: "new_order", label: "New website order", hint: "As soon as a shopper places an order." },
  { key: "low_stock", label: "Low stock", hint: "When a product falls below its low-stock threshold, at most once a day per product." },
  { key: "balance_due", label: "Balance due", hint: "A daily summary of deposits whose balance is past its due date." },
];

const STATUS: Record<EmailSettings["recent"][number]["status"], string> = {
  queued: "Waiting",
  sending: "Sending",
  sent: "Sent",
  failed: "Failed",
  suppressed: "Not sent",
};

const when = (iso: string) => new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) + " UTC";

/** Settings › Emails (Slices D1, D2): who sends, replies, owner alerts, customer emails, a test, the latest emails. */
export function EmailsCard({ settings }: { settings: EmailSettings }) {
  const toast = useToast();
  const update = useUpdateEmailSettings();
  const replyTo = useRequestReplyTo();
  const test = useSendTestEmail();
  const [alerts, setAlerts] = useState<OwnerAlerts>(settings.alerts);
  const [address, setAddress] = useState("");
  const [addressError, setAddressError] = useState<string | null>(null);

  function saveAlerts(event: FormEvent) {
    event.preventDefault();
    update.mutate({ alerts }, { onSuccess: () => toast({ title: "Email alerts saved", variant: "success" }) });
  }

  function askReplyTo(event: FormEvent) {
    event.preventDefault();
    const value = address.trim();
    if (!EMAIL.test(value)) {
      setAddressError("Enter an email address like you@example.com.");
      return;
    }
    setAddressError(null);
    replyTo.mutate(value, {
      onSuccess: () => {
        setAddress("");
        toast({ title: `Confirmation link sent to ${value}`, variant: "success" });
      },
    });
  }

  return (
    <section className="settings-card emails-card" aria-labelledby="emails-heading">
      <h2 id="emails-heading">Emails</h2>
      {settings.delayed && (
        <p className="notice" role="status">
          Emails are delayed: some have waited more than 15 minutes. They are kept and go out once sending works again.
        </p>
      )}
      <dl className="facts emails-card__facts">
        <div>
          <dt>Sending</dt>
          <dd>{PROVIDER[settings.provider]}</dd>
        </div>
        <div>
          <dt>Emails come from</dt>
          <dd>{settings.from_address}</dd>
        </div>
        <div>
          <dt>Replies go to</dt>
          <dd>
            {settings.reply_to ?? <span className="muted">Not set yet: replies would go to an address nobody reads</span>}
            {settings.reply_to_pending && <span className="field-hint"> Waiting for confirmation: {settings.reply_to_pending}</span>}
          </dd>
        </div>
      </dl>

      <form className="record-form emails-card__form" onSubmit={askReplyTo} noValidate aria-labelledby="reply-to-heading">
        <h3 id="reply-to-heading">Reply-to address</h3>
        <p className="muted">
          Customers who reply to your emails reach this address, for example your shop&apos;s Gmail. We email it a link; it is used only once someone
          opens that link and confirms.
        </p>
        <Field id="reply-to" label="Email for replies" error={addressError ?? undefined}>
          <input
            id="reply-to"
            type="email"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            aria-invalid={addressError ? true : undefined}
            aria-describedby={addressError ? "reply-to-error" : undefined}
            disabled={replyTo.isPending}
          />
        </Field>
        {replyTo.isError && (
          <p className="form-error" role="alert">
            {message(replyTo.error)}
          </p>
        )}
        <Button type="submit" variant="ghost" disabled={replyTo.isPending}>
          {replyTo.isPending ? "Sending the link…" : "Send confirmation link"}
        </Button>
      </form>

      <form className="record-form emails-card__form" onSubmit={saveAlerts} aria-labelledby="alerts-heading">
        <h3 id="alerts-heading">Alerts to you</h3>
        {ALERTS.map((a) => (
          <label key={a.key} className="check emails-card__check">
            <input type="checkbox" checked={alerts[a.key]} onChange={(e) => setAlerts({ ...alerts, [a.key]: e.target.checked })} disabled={update.isPending} />
            <span>
              {a.label}
              <span className="field-hint emails-card__hint">{a.hint}</span>
            </span>
          </label>
        ))}
        {update.isError && (
          <p className="form-error" role="alert">
            {message(update.error)}
          </p>
        )}
        <Button type="submit" disabled={update.isPending}>
          {update.isPending ? "Saving…" : "Save alerts"}
        </Button>
      </form>

      <CustomerEmailsForm emails={settings.customer} />

      <div className="emails-card__test">
        <Button
          type="button"
          variant="ghost"
          disabled={test.isPending}
          onClick={() =>
            test.mutate(undefined, {
              onSuccess: (out) =>
                toast({
                  title: out.status === "suppressed" ? "Test not sent: today's limit is reached" : `Test email on its way to ${out.sent_to}`,
                  variant: out.status === "suppressed" ? "error" : "success",
                }),
            })
          }
        >
          {test.isPending ? "Sending…" : "Send a test to myself"}
        </Button>
        <span className="field-hint">It goes to the email you sign in with, nowhere else.</span>
        {test.isError && (
          <p className="form-error" role="alert">
            {message(test.error)}
          </p>
        )}
      </div>

      <h3>Latest emails</h3>
      {settings.recent.length === 0 ? (
        <p className="muted">No emails yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-grid">
            <caption className="visually-hidden">Latest emails</caption>
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">To</th>
                <th scope="col">Subject</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {settings.recent.map((m, i) => (
                <tr key={`${m.created_at}-${i}`}>
                  <td data-label="When">{when(m.created_at)}</td>
                  <td data-label="To">{m.recipient}</td>
                  <td data-label="Subject">{m.subject}</td>
                  <td data-label="Status">
                    <span className={m.status === "sent" ? "tag tag--ok" : m.status === "failed" ? "tag tag--warn" : "tag"}>{STATUS[m.status]}</span>
                    {m.last_error && <span className="field-hint"> {m.last_error}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
