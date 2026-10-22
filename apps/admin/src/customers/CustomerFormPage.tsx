import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { ErrorState } from "../components/ErrorState";
import { Field } from "../components/Field";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { useToast } from "../components/toastContext";
import { customerName, PAYMENT_TERMS, useCreateCustomer, useCustomer, useUpdateCustomer, type Customer } from "./api";
import {
  changedFields,
  EMPTY_FORM,
  toFormValues,
  validateCustomerForm,
  type CustomerFormErrors,
  type CustomerFormValues,
} from "./customerForm";
import { DeleteCustomerDialog } from "./DeleteCustomerDialog";

type TextKey = {
  [K in keyof CustomerFormValues]: CustomerFormValues[K] extends string ? K : never;
}[keyof CustomerFormValues];

function CustomerForm({
  initial,
  submitLabel,
  busy,
  serverError,
  onSubmit,
}: {
  initial: CustomerFormValues;
  submitLabel: string;
  busy: boolean;
  serverError: string | null;
  onSubmit: (values: CustomerFormValues, setErrors: (e: CustomerFormErrors) => void) => void;
}) {
  const [values, setValues] = useState(initial);
  const [errors, setErrors] = useState<CustomerFormErrors>({});
  const set = <K extends keyof CustomerFormValues>(key: K, value: CustomerFormValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }));
  const text = (key: TextKey, props: Record<string, unknown> = {}, hasHint = false) => ({
    id: key,
    value: values[key],
    onChange: (e: { target: { value: string } }) => set(key, e.target.value),
    "aria-invalid": Boolean(errors[key]),
    "aria-describedby": errors[key] ? `${key}-error` : hasHint ? `${key}-hint` : undefined,
    disabled: busy,
    ...props,
  });
  const wholesale = values.customerType === "wholesale";

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(values, setErrors);
  }

  return (
    <form className="record-form" onSubmit={submit} noValidate>
      {serverError && (
        <p className="form-error" role="alert">
          {serverError}
        </p>
      )}
      <fieldset>
        <legend>Customer type</legend>
        <div className="choice-row">
          {(["retail", "wholesale"] as const).map((type) => (
            <label key={type} className="check">
              <input
                type="radio"
                name="customerType"
                value={type}
                checked={values.customerType === type}
                onChange={() => set("customerType", type)}
                disabled={busy}
              />
              {type === "retail" ? "Retail" : "Wholesale (a shop that resells)"}
            </label>
          ))}
        </div>
      </fieldset>

      {wholesale && (
        <fieldset>
          <legend>Wholesale</legend>
          <div className="form-grid">
            <Field id="companyName" label="Company name" error={errors.companyName}>
              <input type="text" maxLength={200} autoComplete="organization" {...text("companyName")} />
            </Field>
            <Field id="discountPercent" label="Wholesale discount (%)" error={errors.discountPercent} hint="Off the regular price, for example 20 or 12.5">
              <input type="text" inputMode="decimal" {...text("discountPercent", {}, true)} />
            </Field>
            <Field id="paymentTerms" label="Payment terms" error={errors.paymentTerms}>
              <select
                id="paymentTerms"
                value={values.paymentTerms}
                onChange={(e) => set("paymentTerms", e.target.value as CustomerFormValues["paymentTerms"])}
                disabled={busy}
              >
                {PAYMENT_TERMS.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <label className="check">
            <input type="checkbox" checked={values.taxExempt} onChange={(e) => set("taxExempt", e.target.checked)} disabled={busy} />
            Tax-exempt (buys for resale; no sales tax is charged)
          </label>
          {values.taxExempt && (
            <Field id="resaleCertificateNumber" label="Resale certificate number" error={errors.resaleCertificateNumber}>
              <input type="text" maxLength={100} autoComplete="off" {...text("resaleCertificateNumber")} />
            </Field>
          )}
        </fieldset>
      )}

      <fieldset>
        <legend>{wholesale ? "Contact person" : "Contact"}</legend>
        <div className="form-grid">
          <Field id="firstName" label="First name" error={errors.firstName}>
            <input type="text" maxLength={200} autoComplete="off" {...text("firstName")} />
          </Field>
          <Field id="lastName" label="Last name (optional)" error={errors.lastName}>
            <input type="text" maxLength={200} autoComplete="off" {...text("lastName")} />
          </Field>
          <Field id="email" label="Email (optional)" error={errors.email}>
            <input type="email" maxLength={254} autoComplete="off" {...text("email")} />
          </Field>
          <Field id="phone" label="Phone (optional)" error={errors.phone}>
            <input type="tel" maxLength={50} autoComplete="off" {...text("phone")} />
          </Field>
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={values.marketingOptIn}
            onChange={(e) => set("marketingOptIn", e.target.checked)}
            disabled={busy}
          />
          Agreed to receive marketing email
        </label>
      </fieldset>

      <fieldset>
        <legend>Address (optional)</legend>
        <div className="form-grid">
          <Field id="addressLine1" label="Street address" error={errors.addressLine1}>
            <input type="text" maxLength={200} autoComplete="off" {...text("addressLine1")} />
          </Field>
          <Field id="addressLine2" label="Apartment, suite (optional)" error={errors.addressLine2}>
            <input type="text" maxLength={200} autoComplete="off" {...text("addressLine2")} />
          </Field>
          <Field id="city" label="City" error={errors.city}>
            <input type="text" maxLength={200} autoComplete="off" {...text("city")} />
          </Field>
          <Field id="state" label="State" error={errors.state}>
            <input type="text" maxLength={100} autoComplete="off" {...text("state")} />
          </Field>
          <Field id="postalCode" label="Postal code" error={errors.postalCode}>
            <input type="text" maxLength={20} autoComplete="off" {...text("postalCode")} />
          </Field>
          <Field id="country" label="Country" error={errors.country} hint="2-letter code, like US">
            <input type="text" maxLength={2} autoComplete="off" {...text("country", {}, true)} />
          </Field>
        </div>
      </fieldset>

      <Field id="notes" label="Notes (optional)" error={errors.notes}>
        <textarea rows={3} maxLength={5000} {...text("notes")} />
      </Field>

      <div className="form-actions">
        <Button type="submit" disabled={busy} aria-busy={busy}>
          {busy ? "Saving…" : submitLabel}
        </Button>
        <Link to="/customers" className="btn-text">
          Cancel
        </Link>
      </div>
    </form>
  );
}

const serverMessage = (e: unknown) => (e instanceof ApiError ? e.message : "The customer couldn't be saved. Try again.");

export function NewCustomerPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const create = useCreateCustomer();
  const [serverError, setServerError] = useState<string | null>(null);
  return (
    <>
      <PageHeader title="New customer" />
      <CustomerForm
        initial={EMPTY_FORM}
        submitLabel="Add customer"
        busy={create.isPending}
        serverError={serverError}
        onSubmit={(values, setErrors) => {
          setServerError(null);
          const { errors, body } = validateCustomerForm(values);
          setErrors(errors);
          if (!body) return;
          create.mutate(body, {
            onSuccess: (customer) => {
              toast({ title: "Customer added", description: customerName(customer), variant: "success" });
              navigate("/customers");
            },
            onError: (e) => setServerError(serverMessage(e)),
          });
        }}
      />
    </>
  );
}

function EditCustomer({ customer, isOwner }: { customer: Customer; isOwner: boolean }) {
  const toast = useToast();
  const navigate = useNavigate();
  const update = useUpdateCustomer(customer.id);
  const [serverError, setServerError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const count = customer.invoice_count;
  return (
    <>
      <PageHeader
        title={customerName(customer)}
        description={`${customer.customer_type === "wholesale" ? "Wholesale" : "Retail"} customer · ${count} ${count === 1 ? "invoice" : "invoices"}`}
        actions={
          isOwner ? (
            <Button type="button" variant="ghost" className="dl-button--danger-ghost" onClick={() => setDeleting(true)}>
              Delete customer
            </Button>
          ) : null
        }
      />
      <CustomerForm
        key={customer.updated_at}
        initial={toFormValues(customer)}
        submitLabel="Save changes"
        busy={update.isPending}
        serverError={serverError}
        onSubmit={(values, setErrors) => {
          setServerError(null);
          const { errors, body } = validateCustomerForm(values);
          setErrors(errors);
          if (!body) return;
          const changes = changedFields(customer, body);
          if (Object.keys(changes).length === 0) {
            toast({ title: "No changes to save" });
            return;
          }
          update.mutate(changes, {
            onSuccess: (saved) => toast({ title: "Saved", description: customerName(saved), variant: "success" }),
            onError: (e) => setServerError(serverMessage(e)),
          });
        }}
      />
      {isOwner && (
        <DeleteCustomerDialog
          customer={customer}
          open={deleting}
          onOpenChange={setDeleting}
          onDeleted={() => navigate("/customers", { replace: true })}
        />
      )}
    </>
  );
}

/** /customers/:customerId: owner and staff edit (staff can't delete). */
export function CustomerPage() {
  const { customerId } = useParams();
  const { state } = useAuth();
  const isOwner = state.status === "signedIn" && state.claims?.appRole === "owner";
  const customer = useCustomer(customerId);

  if (customer.isPending) return <Loading label="Loading customer…" />;
  if (customer.isError) {
    const notFound = customer.error instanceof ApiError && customer.error.status === 404;
    return notFound ? (
      <>
        <PageHeader title="Customer not found" />
        <Link to="/customers" className="btn-text">
          Back to customers
        </Link>
      </>
    ) : (
      <ErrorState title="The customer couldn't be loaded" error={customer.error} onRetry={() => void customer.refetch()} />
    );
  }
  return <EditCustomer customer={customer.data} isOwner={isOwner} />;
}

