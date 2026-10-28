import { Link } from "react-router-dom";
import { ErrorState } from "../components/ErrorState";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { useEmailSettings, type CustomerEmails, type OwnerAlerts } from "../settings/emailApi";
import "./automations.css";

type Row = { name: string; to: string; when: string; on: (s: { alerts: OwnerAlerts; customer: CustomerEmails }) => boolean };

/** Every automatic email, what sends it, and whether it is on. Changed in Settings › Emails only. */
const ROWS: Row[] = [
  { name: "New website order", to: "You", when: "A shopper places an order on your website.", on: (s) => s.alerts.new_order },
  { name: "Low stock", to: "You", when: "A product falls below its low-stock threshold (at most once a day per product).", on: (s) => s.alerts.low_stock },
  { name: "Balance due", to: "You", when: "Every day: deposits whose balance is past its due date.", on: (s) => s.alerts.balance_due },
  { name: "Order received", to: "The shopper", when: "A shopper places an order on your website.", on: (s) => s.customer.order_received.enabled },
  { name: "Deposit received", to: "The shopper", when: "A deposit is received on a website order.", on: (s) => s.customer.deposit_received.enabled },
  { name: "In production", to: "The shopper", when: "Production starts on a website order (once per order).", on: (s) => s.customer.in_production.enabled },
  { name: "Payment received", to: "The shopper", when: "A website order is paid in full.", on: (s) => s.customer.payment_received.enabled },
  { name: "Shipped", to: "The shopper", when: "You mark a website order shipped.", on: (s) => s.customer.shipped.enabled },
];

/** Automations (owner): read only. */
export function AutomationsPage() {
  const emails = useEmailSettings();
  return (
    <>
      <PageHeader
        title="Automations"
        description="What DELEA sends by itself, and when. Read only."
        actions={
          <Link to="/settings#emails-heading" className="dl-button">
            Change in Settings › Emails
          </Link>
        }
      />
      {emails.isPending && <Loading label="Loading automations…" />}
      {emails.isError && <ErrorState title="Automations couldn't be loaded" error={emails.error} onRetry={() => void emails.refetch()} />}
      {emails.data && (
        <section className="settings-card" aria-labelledby="automations-heading">
          <h2 id="automations-heading">Automatic emails</h2>
          <div className="table-wrap">
            <table className="data-grid automations-table">
              <caption className="visually-hidden">Automatic emails</caption>
              <thead>
                <tr>
                  <th scope="col">Email</th>
                  <th scope="col">Goes to</th>
                  <th scope="col">Sent when</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {ROWS.map((row) => {
                  const on = row.on(emails.data);
                  return (
                    <tr key={row.name}>
                      <th scope="row" data-label="Email">
                        {row.name}
                      </th>
                      <td data-label="Goes to">{row.to}</td>
                      <td data-label="Sent when">{row.when}</td>
                      <td data-label="Status">
                        <span className={on ? "tag tag--ok" : "tag"}>{on ? "On" : "Off"}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="muted">
            Emails to shoppers are for website orders only and never include their address or phone number. Demo shops never email shoppers.
          </p>
        </section>
      )}
    </>
  );
}
