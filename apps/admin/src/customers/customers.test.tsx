import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";
import { customerServer, makeCustomer } from "./testServer";

const AVA = makeCustomer({
  first_name: "Ava",
  last_name: "Thompson",
  email: "ava.thompson@example.com",
  phone: "555-0110",
  address_line1: "214 Maple Avenue",
  city: "Austin",
  state: "TX",
  postal_code: "78704",
  invoice_count: 4,
});
const HARBOR = makeCustomer({
  customer_type: "wholesale",
  first_name: "Daniel",
  last_name: "Price",
  company_name: "Harbor Lane Gifts",
  email: "orders@harborlanegifts.example.com",
  phone: "555-0141",
  tax_exempt: true,
  resale_certificate_number: "RC-EXAMPLE-0427",
  wholesale_discount_bps: 2500,
  payment_terms: "net_15",
  invoice_count: 1,
});
const ZOE = makeCustomer({ first_name: "Zoe", last_name: "Bennett", email: "zoe.bennett@example.com", invoice_count: 0 });
const PERSONAL = ["ava.thompson@example.com", "555-0110", "214 Maple Avenue", "Austin", "78704", "orders@harborlanegifts", "555-0141", "zoe.bennett@example.com"];

function renderCustomers(path = "/customers", server = customerServer([AVA, HARBOR, ZOE]), role: "owner" | "staff" | "cpa" = "owner") {
  const utils = renderApp({ path, fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { ...utils, server };
}

const lastList = (server: ReturnType<typeof customerServer>) => server.last("GET", "/api/v1/customers")!.query;
const rowOf = async (name: string) => within((await screen.findByRole("link", { name })).closest("tr")!);

// Personal data must never be logged, whatever happens.
let consoleSpies: MockInstance[] = [];
beforeEach(() => {
  consoleSpies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m));
});
afterEach(() => {
  const logged = consoleSpies.flatMap((spy) => spy.mock.calls.map((args) => args.map(String).join(" ")));
  for (const value of PERSONAL) expect(logged.filter((line) => line.includes(value))).toEqual([]);
  consoleSpies.forEach((spy) => spy.mockRestore());
});

describe("customers list", () => {
  it("shows name, type, terms and invoice count, and no contact details", async () => {
    renderCustomers();
    const ava = await rowOf("Ava Thompson");
    expect(ava.getByText("Retail")).toBeInTheDocument();
    expect(ava.getByText("4")).toBeInTheDocument();

    const harbor = await rowOf("Harbor Lane Gifts");
    expect(harbor.getByText("Wholesale")).toBeInTheDocument();
    expect(harbor.getByText("Tax-exempt")).toBeInTheDocument();
    expect(harbor.getByText("Contact: Daniel Price")).toBeInTheDocument();
    expect(harbor.getByText(/Net 15/)).toBeInTheDocument();
    expect(harbor.getByText(/25% off/)).toBeInTheDocument();
    expect(harbor.getByText("1")).toBeInTheDocument();

    const page = document.body.textContent ?? "";
    for (const value of PERSONAL) expect(page).not.toContain(value);
  });

  it("search and type filter are kept in the URL and sent to the API", async () => {
    const { server, location } = renderCustomers();
    await rowOf("Ava Thompson");
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Search"), "harbor");
    await waitFor(() => expect(lastList(server).get("q")).toBe("harbor"));
    await waitFor(() => expect(location.search).toContain("q=harbor"));
    expect(screen.queryByRole("link", { name: "Ava Thompson" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await user.selectOptions(screen.getByLabelText("Type"), "wholesale");
    await waitFor(() => expect(lastList(server).get("customer_type")).toBe("wholesale"));
    expect(location.search).toBe("?type=wholesale");
    expect(await screen.findByRole("link", { name: "Harbor Lane Gifts" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Zoe Bennett" })).not.toBeInTheDocument();
  });

  it("a search that looks like an email or phone number is never put in a URL", async () => {
    const { server, location } = renderCustomers();
    await rowOf("Ava Thompson");
    const user = userEvent.setup();
    const before = server.calls.length;

    await user.type(screen.getByLabelText("Search"), "ava.thompson@example.com");
    expect(await screen.findByText(/Email addresses and phone numbers aren't searched here/)).toBeInTheDocument();
    expect(screen.getByLabelText("Search")).toHaveValue("ava.thompson@example.com"); // what was typed stays
    await user.clear(screen.getByLabelText("Search"));
    await user.type(screen.getByLabelText("Search"), "555-0110");
    await waitFor(() => expect(screen.getByText(/aren't searched here/)).toBeInTheDocument());
    await new Promise((r) => setTimeout(r, 400)); // past the search debounce

    expect(location.search).toBe("");
    const urls = server.calls.slice(before).map((c) => c.query.toString());
    for (const value of ["ava.thompson", "example.com", "555-0110", "5550110"]) {
      expect(urls.join(" ")).not.toContain(value);
    }
  });

  it("a refused search after an applied one clears the filter and keeps the typed text", async () => {
    const { server, location } = renderCustomers();
    await rowOf("Ava Thompson");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Search"), "harbor");
    await waitFor(() => expect(location.search).toContain("q=harbor"));
    await user.clear(screen.getByLabelText("Search"));
    await user.type(screen.getByLabelText("Search"), "orders@harbor.example.com");
    expect(await screen.findByText(/aren't searched here/)).toBeInTheDocument();
    await waitFor(() => expect(location.search).toBe(""));
    expect(screen.getByLabelText("Search")).toHaveValue("orders@harbor.example.com");
    expect(await screen.findByRole("link", { name: "Zoe Bennett" })).toBeInTheDocument(); // filter cleared
    expect(server.calls.map((c) => c.query.toString()).join(" ")).not.toContain("example.com");

    // Typing a name again applies it and hides the hint.
    await user.clear(screen.getByLabelText("Search"));
    await user.type(screen.getByLabelText("Search"), "zoe");
    await waitFor(() => expect(location.search).toContain("q=zoe"));
    expect(screen.queryByText(/aren't searched here/)).not.toBeInTheDocument();
  });

  it("pages through results 25 at a time", async () => {
    const many = Array.from({ length: 27 }, (_, i) => makeCustomer({ first_name: "Pat", last_name: `Shopper ${String(i + 1).padStart(2, "0")}` }));
    const { server } = renderCustomers("/customers", customerServer(many));
    expect(await screen.findByRole("link", { name: "Pat Shopper 01" })).toBeInTheDocument();
    expect(lastList(server).get("limit")).toBe("26");
    await userEvent.setup().click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("link", { name: "Pat Shopper 26" })).toBeInTheDocument();
    expect(lastList(server).get("offset")).toBe("25");
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("empty list, no matches, and an error with retry", async () => {
    renderCustomers("/customers", customerServer([]));
    expect(await screen.findByRole("heading", { name: "No customers yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add a customer" })).toHaveAttribute("href", "/customers/new");
  });

  it("no matches offers to clear the filters", async () => {
    renderCustomers("/customers?q=nobody");
    expect(await screen.findByRole("heading", { name: "No customers match these filters" })).toBeInTheDocument();
  });

  it("shows the error with a retry", async () => {
    const server = customerServer([AVA]);
    let recovered = false;
    server.override("GET /api/v1/customers", () => (recovered ? undefined : jsonResponse(503, { detail: "Database is temporarily unavailable" })));
    renderCustomers("/customers", server);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Database is temporarily unavailable");
    recovered = true;
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: "Ava Thompson" })).toBeInTheDocument();
  });
});

describe("create", () => {
  it("a retail customer: no wholesale fields on screen, defaults sent, the toast names only", async () => {
    const { server, location } = renderCustomers("/customers/new");
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "New customer" });
    expect(screen.queryByLabelText("Company name")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Wholesale discount/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Tax-exempt/)).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("First name"), "Grace");
    await user.type(screen.getByLabelText("Last name (optional)"), "Kim");
    await user.type(screen.getByLabelText("Email (optional)"), "grace.kim@example.com");
    await user.type(screen.getByLabelText("Phone (optional)"), "555-0116");
    await user.click(screen.getByRole("button", { name: "Add customer" }));

    await waitFor(() => expect(location.pathname).toBe("/customers"));
    expect(server.last("POST", "/api/v1/customers")?.body).toMatchObject({
      customer_type: "retail",
      first_name: "Grace",
      email: "grace.kim@example.com",
      phone: "555-0116",
      company_name: null,
      tax_exempt: false,
      resale_certificate_number: null,
      wholesale_discount_bps: 0,
      payment_terms: "due_on_receipt",
    });
    const toast = await screen.findByText("Customer added");
    const toastText = toast.closest("li")?.textContent ?? "";
    expect(toastText).toContain("Grace Kim");
    expect(toastText).not.toContain("grace.kim@example.com");
    expect(toastText).not.toContain("555-0116");
    expect(location.search).toBe("");
  });

  it("a wholesale customer: fields appear, discount % becomes basis points, tax-exempt needs a certificate", async () => {
    const { server } = renderCustomers("/customers/new");
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "New customer" });
    await user.click(screen.getByLabelText(/Wholesale/));
    await user.type(screen.getByLabelText("First name"), "Laura");

    await user.click(screen.getByRole("button", { name: "Add customer" }));
    expect(screen.getByText("A wholesale customer needs a company name.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Company name"), "Maple Row Mercantile");
    await user.clear(screen.getByLabelText("Wholesale discount (%)"));
    await user.type(screen.getByLabelText("Wholesale discount (%)"), "12.5");
    await user.selectOptions(screen.getByLabelText("Payment terms"), "net_30");
    await user.click(screen.getByLabelText(/Tax-exempt/));
    await user.click(screen.getByRole("button", { name: "Add customer" }));
    expect(screen.getByText("A tax-exempt customer needs a resale certificate number.")).toBeInTheDocument();
    expect(server.last("POST", "/api/v1/customers")).toBeUndefined();

    await user.type(screen.getByLabelText("Resale certificate number"), "RC-123");
    await user.click(screen.getByRole("button", { name: "Add customer" }));
    await waitFor(() =>
      expect(server.last("POST", "/api/v1/customers")?.body).toMatchObject({
        customer_type: "wholesale",
        company_name: "Maple Row Mercantile",
        wholesale_discount_bps: 1250,
        payment_terms: "net_30",
        tax_exempt: true,
        resale_certificate_number: "RC-123",
      }),
    );
  });

  it("an invalid discount is refused before sending", async () => {
    const { server } = renderCustomers("/customers/new");
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "New customer" });
    await user.click(screen.getByLabelText(/Wholesale/));
    await user.type(screen.getByLabelText("First name"), "Laura");
    await user.type(screen.getByLabelText("Company name"), "Maple Row");
    await user.clear(screen.getByLabelText("Wholesale discount (%)"));
    await user.type(screen.getByLabelText("Wholesale discount (%)"), "12.345");
    await user.click(screen.getByRole("button", { name: "Add customer" }));
    expect(screen.getByText("Use at most 2 decimal places.")).toBeInTheDocument();
    expect(screen.getByLabelText("Wholesale discount (%)")).toHaveAttribute("aria-invalid", "true");
    expect(server.last("POST", "/api/v1/customers")).toBeUndefined();
  });
});

describe("edit", () => {
  it("shows the invoice count and sends only the changed fields", async () => {
    const { server } = renderCustomers(`/customers/${HARBOR.id}`);
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { level: 1, name: "Harbor Lane Gifts" })).toBeInTheDocument();
    expect(screen.getByText("Wholesale customer · 1 invoice")).toBeInTheDocument();
    expect(screen.getByLabelText("Wholesale discount (%)")).toHaveValue("25");

    await user.clear(screen.getByLabelText("Wholesale discount (%)"));
    await user.type(screen.getByLabelText("Wholesale discount (%)"), "27.5");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(server.last("PATCH", `/api/v1/customers/${HARBOR.id}`)?.body).toEqual({ wholesale_discount_bps: 2750 }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("switching to retail hides the wholesale fields and resets them on save", async () => {
    const { server } = renderCustomers(`/customers/${HARBOR.id}`);
    const user = userEvent.setup();
    await screen.findByRole("heading", { level: 1, name: "Harbor Lane Gifts" });
    await user.click(screen.getByLabelText("Retail"));
    expect(screen.queryByLabelText("Company name")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(server.last("PATCH", `/api/v1/customers/${HARBOR.id}`)?.body).toEqual({
        customer_type: "retail",
        company_name: null,
        tax_exempt: false,
        resale_certificate_number: null,
        wholesale_discount_bps: 0,
        payment_terms: "due_on_receipt",
      }),
    );
  });

  it("saving without changes sends nothing", async () => {
    const { server } = renderCustomers(`/customers/${AVA.id}`);
    await screen.findByRole("heading", { level: 1, name: "Ava Thompson" });
    await userEvent.setup().click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("No changes to save")).toBeInTheDocument();
    expect(server.last("PATCH", `/api/v1/customers/${AVA.id}`)).toBeUndefined();
  });
});

describe("delete", () => {
  it("a customer with invoices: the server's plain-language 409 is shown and they stay", async () => {
    const { server } = renderCustomers();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete Ava Thompson" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Delete this customer?" }));
    await user.click(dialog.getByRole("button", { name: "Delete customer" }));
    expect(await dialog.findByRole("alert")).toHaveTextContent(
      "This customer has 4 invoice(s), so they can't be deleted. Their invoices stay linked to them to keep your sales history complete.",
    );
    expect(server.customers.has(AVA.id)).toBe(true);
  });

  it("a customer without invoices is deleted after confirmation", async () => {
    const { server } = renderCustomers();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete Zoe Bennett" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete customer" }));
    await waitFor(() => expect(screen.queryByRole("link", { name: "Zoe Bennett" })).not.toBeInTheDocument());
    expect(server.customers.has(ZOE.id)).toBe(false);
    const toast = (await screen.findByText("Customer deleted")).closest("li")?.textContent ?? "";
    expect(toast).not.toContain("zoe.bennett@example.com");
  });

  it("from the customer page, the owner is sent back to the list", async () => {
    const { location } = renderCustomers(`/customers/${ZOE.id}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete customer" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete customer" }));
    await waitFor(() => expect(location.pathname).toBe("/customers"));
  });
});

describe("roles", () => {
  it("staff can add and edit but has no Delete anywhere", async () => {
    const { server } = renderCustomers("/customers", undefined, "staff");
    await rowOf("Ava Thompson");
    expect(screen.getByText("You can add and edit customers; only the owner can delete them.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New customer" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Delete/ })).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("link", { name: "Edit Ava Thompson" }));
    await screen.findByRole("heading", { level: 1, name: "Ava Thompson" });
    expect(screen.queryByRole("button", { name: "Delete customer" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Notes (optional)"), "Likes gold");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(server.last("PATCH", `/api/v1/customers/${AVA.id}`)?.body).toEqual({ notes: "Likes gold" }));
  });

  it.each(["/customers", "/customers/new", `/customers/${AVA.id}`])("cpa is blocked from %s", async (path) => {
    const { server } = renderCustomers(path, undefined, "cpa");
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("You don't have access to this page");
    expect(server.calls.some((c) => c.path.startsWith("/api/v1/customers"))).toBe(false);
  });
});
