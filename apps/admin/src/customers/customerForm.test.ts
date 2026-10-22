import { describe, expect, it } from "vitest";
import {
  bpsToPercent,
  changedFields,
  EMPTY_FORM,
  looksLikeContactDetails,
  percentToBps,
  toFormValues,
  validateCustomerForm,
} from "./customerForm";
import { makeCustomer } from "./testServer";

describe("discount percent <-> basis points (no floating point)", () => {
  it.each([
    ["20", 2000],
    ["12.5", 1250],
    ["12.50", 1250],
    ["0.05", 5],
    ["0", 0],
    ["", 0],
    ["100", 10000],
    ["33.33", 3333],
    ["7%", 700],
  ])("%s%% -> %d bps", (text, bps) => expect(percentToBps(text)).toEqual({ bps }));

  it.each([
    ["-5", "The discount can't be negative."],
    ["12.345", "Use at most 2 decimal places."],
    ["100.01", "The discount can't be more than 100%."],
    ["abc", "Enter a percentage like 20 or 12.5."],
    ["1,5", "Enter a percentage like 20 or 12.5."],
  ])("%s is refused: %s", (text, error) => expect(percentToBps(text)).toEqual({ error }));

  it.each([
    [2000, "20"],
    [1250, "12.5"],
    [5, "0.05"],
    [0, "0"],
    [10000, "100"],
    [3333, "33.33"],
  ])("%d bps -> %s%%", (bps, text) => expect(bpsToPercent(bps)).toBe(text));

  it("round-trips every basis-point value exactly", () => {
    for (let bps = 0; bps <= 10000; bps++) expect(percentToBps(bpsToPercent(bps)).bps).toBe(bps);
  });
});

describe("validateCustomerForm", () => {
  const retail = { ...EMPTY_FORM, firstName: " Ava ", lastName: "Thompson", email: "ava@example.com" };
  const wholesale = {
    ...EMPTY_FORM,
    customerType: "wholesale" as const,
    firstName: "Rachel",
    companyName: "Willow & Thread",
    discountPercent: "30",
    paymentTerms: "net_30" as const,
  };

  it("a retail body keeps every wholesale field at its default, whatever the form holds", () => {
    const { body } = validateCustomerForm({
      ...retail,
      companyName: "Left over",
      taxExempt: true,
      resaleCertificateNumber: "RC-1",
      discountPercent: "40",
      paymentTerms: "net_60",
    });
    expect(body).toMatchObject({
      customer_type: "retail",
      first_name: "Ava",
      email: "ava@example.com",
      phone: null,
      company_name: null,
      tax_exempt: false,
      resale_certificate_number: null,
      wholesale_discount_bps: 0,
      payment_terms: "due_on_receipt",
    });
  });

  it("a wholesale body carries the company, discount in bps and terms", () => {
    const { errors, body } = validateCustomerForm({ ...wholesale, taxExempt: true, resaleCertificateNumber: " RC-0427 " });
    expect(errors).toEqual({});
    expect(body).toMatchObject({
      customer_type: "wholesale",
      company_name: "Willow & Thread",
      wholesale_discount_bps: 3000,
      payment_terms: "net_30",
      tax_exempt: true,
      resale_certificate_number: "RC-0427",
    });
  });

  it.each([
    [{ firstName: "" }, "firstName", "Enter a first name."],
    [{ firstName: "x".repeat(201) }, "firstName", "First name can be at most 200 characters."],
    [{ email: "not-an-email" }, "email", "Enter an email address like name@example.com."],
    [{ phone: "1".repeat(51) }, "phone", "Phone can be at most 50 characters."],
    [{ postalCode: "1".repeat(21) }, "postalCode", "Postal code can be at most 20 characters."],
    [{ country: "USA" }, "country", "Use a 2-letter country code, like US."],
    [{ notes: "x".repeat(5001) }, "notes", "Notes can be at most 5000 characters."],
  ])("retail %o -> %s", (change, field, message) => {
    const { errors, body } = validateCustomerForm({ ...retail, ...change });
    expect(errors[field as keyof typeof errors]).toBe(message);
    expect(body).toBeUndefined();
  });

  it.each([
    [{ companyName: " " }, "companyName", "A wholesale customer needs a company name."],
    [{ taxExempt: true, resaleCertificateNumber: "" }, "resaleCertificateNumber", "A tax-exempt customer needs a resale certificate number."],
    [{ discountPercent: "101" }, "discountPercent", "The discount can't be more than 100%."],
    [{ resaleCertificateNumber: "x".repeat(101) }, "resaleCertificateNumber", "Resale certificate number can be at most 100 characters."],
  ])("wholesale %o -> %s", (change, field, message) => {
    const { errors } = validateCustomerForm({ ...wholesale, ...change });
    expect(errors[field as keyof typeof errors]).toBe(message);
  });

  it("the country is upper-cased", () => {
    expect(validateCustomerForm({ ...retail, country: "ca" }).body?.country).toBe("CA");
  });
});

describe("editing", () => {
  const customer = makeCustomer({
    customer_type: "wholesale",
    first_name: "Daniel",
    last_name: "Price",
    company_name: "Harbor Lane Gifts",
    tax_exempt: true,
    resale_certificate_number: "RC-EXAMPLE-0427",
    wholesale_discount_bps: 2500,
    payment_terms: "net_15",
    email: "orders@harborlanegifts.example.com",
  });

  it("round-trips without changes", () => {
    const values = toFormValues(customer);
    expect(values.discountPercent).toBe("25");
    expect(changedFields(customer, validateCustomerForm(values).body!)).toEqual({});
  });

  it("sends only what changed", () => {
    const { body } = validateCustomerForm({ ...toFormValues(customer), phone: "555-0140", discountPercent: "27.5" });
    expect(changedFields(customer, body!)).toEqual({ phone: "555-0140", wholesale_discount_bps: 2750 });
  });

  it("switching to retail resets every wholesale field", () => {
    const { body } = validateCustomerForm({ ...toFormValues(customer), customerType: "retail" });
    expect(changedFields(customer, body!)).toEqual({
      customer_type: "retail",
      company_name: null,
      tax_exempt: false,
      resale_certificate_number: null,
      wholesale_discount_bps: 0,
      payment_terms: "due_on_receipt",
    });
  });
});

describe("looksLikeContactDetails", () => {
  it.each([
    ["ava@example.com", true],
    ["@", true],
    ["555-0123", true],
    ["Suite 1234", false], // four digits: not treated as a phone number
    ["(512) 555 0100", true],
    ["Ava", false],
    ["Harbor Lane", false],
    ["Shop 42", false],
  ])("%s -> %s", (q, expected) => expect(looksLikeContactDetails(q)).toBe(expected));
});
