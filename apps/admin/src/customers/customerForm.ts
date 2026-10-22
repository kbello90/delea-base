import type { Customer, CustomerCreate, CustomerType, CustomerUpdate, PaymentTerms } from "./api";

/**
 * The customer form. Validation mirrors the API (backend/app/models/customer.py) and the
 * database constraints on public.customers:
 * - a retail customer keeps every wholesale-only field at its default (no company, not
 *   tax-exempt, no resale certificate, 0% discount, due on receipt);
 * - a wholesale customer needs a company name;
 * - a tax-exempt customer needs a resale certificate number;
 * - the discount is 0 to 100% with at most 2 decimals, sent as basis points (string math).
 */
export interface CustomerFormValues {
  customerType: CustomerType;
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  addressLine1: string;
  addressLine2: string;
  city: string;
  state: string;
  postalCode: string;
  country: string;
  notes: string;
  marketingOptIn: boolean;
  companyName: string;
  taxExempt: boolean;
  resaleCertificateNumber: string;
  discountPercent: string;
  paymentTerms: PaymentTerms;
}

export type CustomerFormErrors = Partial<Record<keyof CustomerFormValues, string>>;

export const EMPTY_FORM: CustomerFormValues = {
  customerType: "retail",
  firstName: "",
  lastName: "",
  email: "",
  phone: "",
  addressLine1: "",
  addressLine2: "",
  city: "",
  state: "",
  postalCode: "",
  country: "US",
  notes: "",
  marketingOptIn: false,
  companyName: "",
  taxExempt: false,
  resaleCertificateNumber: "",
  discountPercent: "0",
  paymentTerms: "due_on_receipt",
};

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/; // the API's own pattern
const PERCENT = /^\d{1,3}(?:\.\d{1,2})?$/;

/**
 * Personal data (an email address or a phone number) must never reach a URL: the page's own
 * address or the API request's query string. A search that looks like one isn't applied.
 */
export function looksLikeContactDetails(q: string): boolean {
  return q.includes("@") || q.replace(/\D/g, "").length >= 5;
}

/** "12.5" -> 1250 basis points, exactly (no floating point). */
export function percentToBps(raw: string): { bps?: number; error?: string } {
  const text = raw.trim().replace(/%$/, "").trim();
  if (!text) return { bps: 0 };
  if (text.startsWith("-")) return { error: "The discount can't be negative." };
  if (/\.\d{3,}$/.test(text)) return { error: "Use at most 2 decimal places." };
  if (!PERCENT.test(text)) return { error: "Enter a percentage like 20 or 12.5." };
  const [whole, fraction = ""] = text.split(".");
  const bps = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (bps > 10_000) return { error: "The discount can't be more than 100%." };
  return { bps };
}

/** 1250 -> "12.5", 3000 -> "30", 5 -> "0.05". */
export function bpsToPercent(bps: number): string {
  const whole = Math.trunc(bps / 100);
  const fraction = String(bps % 100).padStart(2, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : String(whole);
}

export function toFormValues(c: Customer): CustomerFormValues {
  return {
    customerType: c.customer_type,
    firstName: c.first_name,
    lastName: c.last_name ?? "",
    email: c.email ?? "",
    phone: c.phone ?? "",
    addressLine1: c.address_line1 ?? "",
    addressLine2: c.address_line2 ?? "",
    city: c.city ?? "",
    state: c.state ?? "",
    postalCode: c.postal_code ?? "",
    country: c.country,
    notes: c.notes ?? "",
    marketingOptIn: c.marketing_opt_in,
    companyName: c.company_name ?? "",
    taxExempt: c.tax_exempt,
    resaleCertificateNumber: c.resale_certificate_number ?? "",
    discountPercent: bpsToPercent(c.wholesale_discount_bps),
    paymentTerms: c.payment_terms,
  };
}

type Body = Required<Omit<CustomerCreate, "customer_type">> & { customer_type: CustomerType };

/** Validates the form and builds the full body (wholesale fields reset for retail). */
export function validateCustomerForm(v: CustomerFormValues): { errors: CustomerFormErrors; body?: Body } {
  const errors: CustomerFormErrors = {};
  const text = (key: keyof CustomerFormValues, max: number, label: string): string | null => {
    const value = (v[key] as string).trim();
    if (value.length > max) errors[key] = `${label} can be at most ${max} characters.`;
    return value || null;
  };

  const firstName = text("firstName", 200, "First name");
  if (!firstName) errors.firstName = "Enter a first name.";
  const lastName = text("lastName", 200, "Last name");
  const email = text("email", 254, "Email");
  if (email && !EMAIL.test(email)) errors.email = "Enter an email address like name@example.com.";
  const phone = text("phone", 50, "Phone");
  const addressLine1 = text("addressLine1", 200, "Address");
  const addressLine2 = text("addressLine2", 200, "Address line 2");
  const city = text("city", 200, "City");
  const state = text("state", 100, "State");
  const postalCode = text("postalCode", 20, "Postal code");
  const notes = text("notes", 5000, "Notes");
  const country = v.country.trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(country)) errors.country = "Use a 2-letter country code, like US.";

  const wholesale = v.customerType === "wholesale";
  let companyName: string | null = null;
  let resale: string | null = null;
  let bps = 0;
  if (wholesale) {
    companyName = text("companyName", 200, "Company name");
    if (!companyName) errors.companyName = "A wholesale customer needs a company name.";
    resale = text("resaleCertificateNumber", 100, "Resale certificate number");
    if (v.taxExempt && !resale) errors.resaleCertificateNumber = "A tax-exempt customer needs a resale certificate number.";
    const discount = percentToBps(v.discountPercent);
    if (discount.error) errors.discountPercent = discount.error;
    bps = discount.bps ?? 0;
  }

  if (Object.keys(errors).length > 0 || !firstName) return { errors };
  return {
    errors,
    body: {
      customer_type: v.customerType,
      first_name: firstName,
      last_name: lastName,
      email,
      phone,
      address_line1: addressLine1,
      address_line2: addressLine2,
      city,
      state,
      postal_code: postalCode,
      country,
      marketing_opt_in: v.marketingOptIn,
      notes,
      // Retail: every wholesale-only field at its default (the API and database require it).
      company_name: wholesale ? companyName : null,
      tax_exempt: wholesale ? v.taxExempt : false,
      resale_certificate_number: wholesale ? resale : null,
      wholesale_discount_bps: wholesale ? bps : 0,
      payment_terms: wholesale ? v.paymentTerms : "due_on_receipt",
    },
  };
}

const FIELDS = [
  "customer_type",
  "first_name",
  "last_name",
  "email",
  "phone",
  "address_line1",
  "address_line2",
  "city",
  "state",
  "postal_code",
  "country",
  "marketing_opt_in",
  "notes",
  "company_name",
  "tax_exempt",
  "resale_certificate_number",
  "wholesale_discount_bps",
  "payment_terms",
] as const;

/** Only the fields that changed, for PATCH. */
export function changedFields(customer: Customer, body: Body): CustomerUpdate {
  const changes: Record<string, unknown> = {};
  for (const field of FIELDS) {
    if (body[field] !== customer[field]) changes[field] = body[field];
  }
  return changes as CustomerUpdate;
}
