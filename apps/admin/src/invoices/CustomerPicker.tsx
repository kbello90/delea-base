import { useState } from "react";
import { customerName, useCustomerSearch, type Customer } from "../customers/api";
import { looksLikeContactDetails } from "../customers/customerForm";

export interface PickedCustomer {
  id: string;
  name: string;
}

/**
 * Pick a customer by name or company. The search text stays in this component (never in a
 * URL), and email- or phone-like text isn't searched. `allowWalkIn` offers "Walk-in" as the
 * empty choice; otherwise it reads "Any customer".
 */
export function CustomerPicker({
  id,
  label = "Customer",
  value,
  onChange,
  allowWalkIn = false,
}: {
  id: string;
  label?: string;
  value: PickedCustomer | null;
  onChange: (customer: PickedCustomer | null) => void;
  allowWalkIn?: boolean;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const search = useCustomerSearch(q, looksLikeContactDetails);
  const refused = q.trim() !== "" && looksLikeContactDetails(q.trim());
  const empty = allowWalkIn ? "Walk-in" : "Any customer";

  function pick(customer: Customer | null) {
    onChange(customer ? { id: customer.id, name: customerName(customer) } : null);
    setQ("");
    setOpen(false);
  }

  if (!open) {
    return (
      <div className="filter picker">
        <span className="picker__label" id={`${id}-label`}>
          {label}
        </span>
        <div className="picker__current" aria-labelledby={`${id}-label`}>
          <span>{value ? value.name : empty}</span>
          <button type="button" className="btn-text" onClick={() => setOpen(true)} aria-label={`Change ${label.toLowerCase()}`}>
            Change
          </button>
          {value && (
            <button type="button" className="btn-text" onClick={() => pick(null)}>
              {allowWalkIn ? "Make it a walk-in sale" : "Clear"}
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="filter picker">
      <label htmlFor={id}>Find a customer</label>
      <input
        id={id}
        type="search"
        placeholder="Name or company"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        autoFocus
      />
      {refused && (
        <span className="field-hint" role="status">
          Search by name or company; email addresses and phone numbers aren't searched.
        </span>
      )}
      <ul className="picker__results" aria-label="Matching customers">
        {search.data?.map((customer) => (
          <li key={customer.id}>
            <button type="button" className="picker__option" onClick={() => pick(customer)}>
              {customerName(customer)}
              {customer.customer_type === "wholesale" && <span className="tag tag--ok">Wholesale</span>}
              {customer.tax_exempt && <span className="tag">Tax-exempt</span>}
            </button>
          </li>
        ))}
        {search.data?.length === 0 && <li className="muted">No customers match.</li>}
      </ul>
      <div className="form-actions">
        {allowWalkIn && (
          <button type="button" className="btn-text" onClick={() => pick(null)}>
            Walk-in (no customer)
          </button>
        )}
        <button type="button" className="btn-text" onClick={() => setOpen(false)}>
          Keep {value ? value.name : empty.toLowerCase()}
        </button>
      </div>
    </div>
  );
}
