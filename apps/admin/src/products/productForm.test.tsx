import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { jsonResponse, renderApp } from "../test/harness";
import { changedFields, EMPTY_FORM, validateProductForm } from "./productForm";
import { makeProduct, productServer } from "./testServer";

describe("validateProductForm", () => {
  const valid = { ...EMPTY_FORM, name: "Ring", sku: "R-1", metal: "platinum" as const, price: "1,250.50" };

  it("builds the body with integer cents", () => {
    const { errors, body } = validateProductForm({ ...valid, salePrice: "999.99", stockQty: "4", lowStockThreshold: "2" });
    expect(errors).toEqual({});
    expect(body).toMatchObject({ price_cents: 125050, sale_price_cents: 99999, stock_qty: 4, low_stock_threshold: 2 });
  });

  it.each([
    [{ name: "" }, "name", "Enter a product name."],
    [{ sku: " " }, "sku", "Enter a SKU."],
    [{ metal: "" as const }, "metal", "Choose a metal."],
    [{ price: "" }, "price", "Enter an amount."],
    [{ price: "12.345" }, "price", "Use at most 2 decimal places."],
    [{ salePrice: "1250.50" }, "salePrice", "The sale price must be lower than the price."],
    [{ saleEndsAt: "2026-12-01T10:00" }, "saleEndsAt", "Set a sale price, or clear the end date."],
    [{ stockQty: "-1" }, "stockQty", "Stock must be a whole number of 0 or more."],
    [{ stockQty: "2.5" }, "stockQty", "Stock must be a whole number of 0 or more."],
    [{ lowStockThreshold: "9999999999" }, "lowStockThreshold", "The low-stock threshold is too large."],
  ])("%o -> %s: %s", (change, field, message) => {
    const { errors, body } = validateProductForm({ ...valid, ...change });
    expect(errors[field as keyof typeof errors]).toBe(message);
    expect(body).toBeUndefined();
  });

  it("an edit sends only what changed", () => {
    const product = makeProduct({
      name: "Ring",
      sku: "R-1",
      metal: "platinum",
      price_cents: 125050,
      stock_qty: 0,
      is_published: false,
    });
    const { body } = validateProductForm({ ...valid, price: "1300" });
    expect(changedFields(product, body!)).toEqual({ price_cents: 130000 });
  });
});

describe("product form", () => {
  it("validates before sending anything", async () => {
    const server = productServer();
    renderApp({ path: "/products/new", fetch: server.fetch });
    await userEvent.setup().click(await screen.findByRole("button", { name: "Create product" }));
    expect(screen.getByText("Enter a product name.")).toBeInTheDocument();
    expect(screen.getByText("Enter a SKU.")).toBeInTheDocument();
    expect(screen.getByText("Choose a metal.")).toBeInTheDocument();
    expect(screen.getByText("Enter an amount.")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
    expect(server.calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("creates a product with integer cents and opens it for images", async () => {
    const server = productServer();
    const { location } = renderApp({ path: "/products/new", fetch: server.fetch });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Name"), "Halo Ring");
    await user.type(screen.getByLabelText("SKU"), "RING-9");
    await user.selectOptions(screen.getByLabelText("Metal"), "gold_18k");
    await user.type(screen.getByLabelText("Price"), "1,250.50");
    await user.type(screen.getByLabelText("Sale price (optional)"), "0.29");
    await user.click(screen.getByLabelText("Published (visible on the storefront)"));
    await user.click(screen.getByRole("button", { name: "Create product" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Halo Ring" })).toBeInTheDocument();
    expect(server.last("POST", "/api/v1/products")?.body).toEqual({
      name: "Halo Ring",
      sku: "RING-9",
      description: null,
      metal: "gold_18k",
      price_cents: 125050,
      sale_price_cents: 29,
      sale_ends_at: null,
      stock_qty: 0,
      low_stock_threshold: 3,
      is_published: true,
      made_to_order: false,
      lead_time_note: null,
      sort_order: 0,
    });
    expect(location.pathname).toMatch(/^\/products\/a/);
    expect(screen.getByRole("heading", { name: "Images" })).toBeInTheDocument();
  });

  async function submitNew(server: ReturnType<typeof productServer>, sku: string) {
    renderApp({ path: "/products/new", fetch: server.fetch });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Name"), "Another");
    await user.type(screen.getByLabelText("SKU"), sku);
    await user.selectOptions(screen.getByLabelText("Metal"), "other");
    await user.type(screen.getByLabelText("Price"), "10");
    await user.click(screen.getByRole("button", { name: "Create product" }));
  }

  it("a duplicate-SKU 409 is shown on the SKU field only", async () => {
    await submitNew(productServer([makeProduct({ sku: "TAKEN" })]), "TAKEN");
    expect(await screen.findByText("This SKU is already used by another product.")).toBeInTheDocument();
    expect(screen.getByLabelText("SKU")).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("any other 409 is a form error with the server's message, not a SKU error", async () => {
    const server = productServer();
    server.override("POST /api/v1/products", () => jsonResponse(409, { detail: "The request conflicts with a business rule" }));
    await submitNew(server, "NEW-1");
    expect(await screen.findByRole("alert")).toHaveTextContent("The request conflicts with a business rule");
    expect(screen.queryByText("This SKU is already used by another product.")).not.toBeInTheDocument();
    expect(screen.getByLabelText("SKU")).toHaveAttribute("aria-invalid", "false");
  });

  it("editing sends a PATCH with only the changed fields", async () => {
    const product = makeProduct({ name: "Halo Ring", price_cents: 125000 });
    const server = productServer([product]);
    renderApp({ path: `/products/${product.id}`, fetch: server.fetch });
    const user = userEvent.setup();
    const price = await screen.findByLabelText("Price");
    expect(price).toHaveValue("1250.00");
    await user.clear(price);
    await user.type(price, "1300");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(server.last("PATCH", `/api/v1/products/${product.id}`)?.body).toEqual({ price_cents: 130000 }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });
});

describe("made to order", () => {
  const valid = { ...EMPTY_FORM, name: "Ring", sku: "R-1", metal: "platinum" as const, price: "100" };

  it("sends the flag and a trimmed lead-time note, or null for none", () => {
    expect(validateProductForm({ ...valid, madeToOrder: true, leadTimeNote: "  Ships in 2-3 weeks " }).body).toMatchObject({
      made_to_order: true,
      lead_time_note: "Ships in 2-3 weeks",
    });
    expect(validateProductForm({ ...valid, leadTimeNote: "   " }).body).toMatchObject({ made_to_order: false, lead_time_note: null });
  });

  it("refuses a lead-time note over 60 characters", () => {
    const { errors, body } = validateProductForm({ ...valid, leadTimeNote: "x".repeat(61) });
    expect(errors.leadTimeNote).toBe("Use at most 60 characters.");
    expect(body).toBeUndefined();
  });

  it("an edit sends only the made-to-order change", () => {
    const product = makeProduct({ name: "Ring", sku: "R-1", metal: "platinum", price_cents: 10000, stock_qty: 0, is_published: false });
    const { body } = validateProductForm({ ...valid, madeToOrder: true, leadTimeNote: "Ships in 2 weeks" });
    expect(changedFields(product, body!)).toEqual({ made_to_order: true, lead_time_note: "Ships in 2 weeks" });
  });

  it("the owner turns it on in the form", async () => {
    const product = makeProduct({ name: "Ring", made_to_order: false });
    const server = productServer([product]);
    renderApp({ path: `/products/${product.id}`, fetch: server.fetch });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: "Made to order" }));
    await user.type(screen.getByLabelText("Lead time (optional, shown on the storefront)"), "Ships in 2-3 weeks");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(server.last("PATCH", `/api/v1/products/${product.id}`)?.body).toEqual({ made_to_order: true, lead_time_note: "Ships in 2-3 weeks" }),
    );
  });
});
