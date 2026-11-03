import { bpsToPercent } from "../customers/customerForm";
import { documentNumber, methodLabel, type Invoice } from "../invoices/api";
import { formatMoney } from "../lib/money";
import type { ReceiptLayout, ReceiptSettings } from "./api";
import { isDepositReceipt, receiptDateTime, receiptTime } from "./receiptFormat";

/**
 * One printable receipt. The same content on every paper: shop, number, date, lines with
 * their personalization, totals, each payment and, on a deposit, the balance still due.
 * Letter lays the lines out as a table; the 80 and 58 mm rolls as a single narrow column.
 */
export function ReceiptDocument({
  invoice,
  settings,
  layout,
  currency,
  timeZone,
}: {
  invoice: Invoice;
  settings: ReceiptSettings;
  layout: ReceiptLayout;
  currency: string;
  timeZone?: string;
}) {
  const money = (cents: number) => formatMoney(cents, currency);
  const deposit = isDepositReceipt(invoice);
  const payments = (invoice.payments ?? []).filter((p) => p.status === "succeeded");
  const lines = invoice.items ?? [];
  const letter = layout === "letter";

  return (
    <article className={`receipt-doc receipt-doc--${layout}`} data-layout={layout} aria-label={deposit ? "Deposit receipt" : "Receipt"}>
      <header className="receipt-doc__shop">
        <p className="receipt-doc__name">{settings.business_name}</p>
        {settings.address && <p className="receipt-doc__address">{settings.address}</p>}
      </header>

      <section className="receipt-doc__heading">
        <h1>{deposit ? "Deposit receipt" : "Receipt"}</h1>
        {deposit && <p className="receipt-doc__warning">Not a sales receipt</p>}
        <dl className="receipt-doc__facts">
          <div>
            <dt>{deposit ? "Quote" : "Invoice"}</dt>
            <dd>{documentNumber(invoice)}</dd>
          </div>
          <div>
            <dt>Date</dt>
            <dd>{receiptDateTime(receiptTime(invoice), timeZone)}</dd>
          </div>
          {invoice.customer_id && (
            <div>
              <dt>Customer</dt>
              <dd>{invoice.customer_name}</dd>
            </div>
          )}
        </dl>
      </section>

      {letter ? (
        <table className="receipt-doc__table">
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col" className="num">
                Qty
              </th>
              <th scope="col" className="num">
                Unit price
              </th>
              <th scope="col" className="num">
                Amount
              </th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.id}>
                <td>
                  {line.description}
                  <Answers line={line} />
                </td>
                <td className="num">{line.qty}</td>
                <td className="num">{money(line.unit_price_cents)}</td>
                <td className="num">{money(line.line_total_cents)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <ul className="receipt-doc__lines">
          {lines.map((line) => (
            <li key={line.id}>
              <div className="receipt-doc__row">
                <span>
                  {line.qty} × {line.description}
                </span>
                <span>{money(line.line_total_cents)}</span>
              </div>
              {line.qty > 1 && <p className="receipt-doc__each">{money(line.unit_price_cents)} each</p>}
              <Answers line={line} />
            </li>
          ))}
        </ul>
      )}

      <dl className="receipt-doc__totals">
        <div>
          <dt>Subtotal</dt>
          <dd>{money(invoice.subtotal_cents)}</dd>
        </div>
        {invoice.shipping_cents > 0 && (
          <div>
            <dt>Shipping</dt>
            <dd>{money(invoice.shipping_cents)}</dd>
          </div>
        )}
        <div>
          <dt>Tax ({bpsToPercent(invoice.tax_rate_bps)}%)</dt>
          <dd>{money(invoice.tax_cents)}</dd>
        </div>
        <div className="receipt-doc__total">
          <dt>Total</dt>
          <dd>{money(invoice.total_cents)}</dd>
        </div>
      </dl>

      <dl className="receipt-doc__payments" aria-label="Payments">
        {payments.map((p) => (
          <div key={p.id}>
            <dt>
              {p.kind === "refund" ? "Refund" : deposit ? "Deposit" : "Paid"} · {methodLabel(p.method)}
            </dt>
            <dd>{money(p.kind === "refund" ? -p.amount_cents : p.amount_cents)}</dd>
          </div>
        ))}
        {deposit && (
          <div className="receipt-doc__total">
            <dt>Balance due</dt>
            <dd>{money(invoice.balance_cents)}</dd>
          </div>
        )}
      </dl>
      {invoice.refunded_at && <p className="receipt-doc__warning">Refunded</p>}
      {invoice.cancelled_at && <p className="receipt-doc__warning">Cancelled</p>}

      {settings.receipt_footer && <footer className="receipt-doc__footer">{settings.receipt_footer}</footer>}
    </article>
  );
}

function Answers({ line }: { line: NonNullable<Invoice["items"]>[number] }) {
  if (!line.personalization?.length) return null;
  return (
    <ul className="receipt-doc__answers">
      {line.personalization.map((a) => (
        <li key={a.label}>
          {a.label}: {a.value}
        </li>
      ))}
    </ul>
  );
}
