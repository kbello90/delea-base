import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";
import { makeImage, makeProduct, productServer } from "./testServer";

const RING = makeProduct({
  name: "Halo Ring",
  sku: "RING-1",
  metal: "gold_14k",
  price_cents: 125000,
  sale_price_cents: 99900,
  stock_qty: 2,
  low_stock_threshold: 3,
  images: [makeImage(0)],
});
const PENDANT = makeProduct({ name: "Moon Pendant", sku: "PEND-1", metal: "silver_925", is_published: false });

function renderProducts(path = "/products", server = productServer([RING, PENDANT]), role: "owner" | "staff" = "owner") {
  const utils = renderApp({ path, fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { ...utils, server };
}

const lastList = (server: ReturnType<typeof productServer>) => server.last("GET", "/api/v1/products")!.query;

describe("products list", () => {
  it("shows each product with thumbnail, price, stock and status", async () => {
    renderProducts();
    const row = (await screen.findByRole("link", { name: "Halo Ring" })).closest("tr")!;
    const cells = within(row);
    expect(cells.getByText("SKU RING-1")).toBeInTheDocument();
    expect(cells.getByText("14k gold")).toBeInTheDocument();
    expect(await cells.findByText("$999.00")).toBeInTheDocument(); // on sale
    expect(cells.getByText("$1,250.00")).toBeInTheDocument();
    expect(cells.getByText("Low stock")).toBeInTheDocument();
    expect(cells.getByText("Published")).toBeInTheDocument();
    expect(row.querySelector("img")).toHaveAttribute("src", RING.images[0].thumb_url);

    const pendant = within(screen.getByRole("link", { name: "Moon Pendant" }).closest("tr")!);
    expect(pendant.getByText("Draft")).toBeInTheDocument();
    expect(pendant.queryByText("Low stock")).not.toBeInTheDocument();
  });

  it("filters by search, metal, status and low stock (in the URL and the request)", async () => {
    const { server, location } = renderProducts();
    await screen.findByRole("link", { name: "Halo Ring" });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Search"), "ring");
    await waitFor(() => expect(lastList(server).get("q")).toBe("ring"));
    expect(screen.queryByRole("link", { name: "Moon Pendant" })).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Metal"), "gold_14k");
    await waitFor(() => expect(lastList(server).get("metal")).toBe("gold_14k"));
    await user.selectOptions(screen.getByLabelText("Status"), "yes");
    await waitFor(() => expect(lastList(server).get("is_published")).toBe("true"));
    await user.click(screen.getByLabelText("Low stock only"));
    await waitFor(() => expect(lastList(server).get("low_stock")).toBe("true"));
    expect(location.pathname).toBe("/products");

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect([...lastList(server).keys()].sort()).toEqual(["limit", "offset"]));
    expect(await screen.findByRole("link", { name: "Moon Pendant" })).toBeInTheDocument();
  });

  it("the dashboard's low-stock link opens the filtered list", async () => {
    const { server } = renderProducts("/products?low_stock=true");
    expect(await screen.findByRole("link", { name: "Halo Ring" })).toBeInTheDocument();
    expect(screen.getByLabelText("Low stock only")).toBeChecked();
    expect(lastList(server).get("low_stock")).toBe("true");
    expect(screen.queryByRole("link", { name: "Moon Pendant" })).not.toBeInTheDocument();
  });

  it("pages through results 25 at a time", async () => {
    const many = Array.from({ length: 28 }, (_, i) => makeProduct({ name: `Item ${String(i + 1).padStart(2, "0")}` }));
    const { server } = renderProducts("/products", productServer(many));
    expect(await screen.findByRole("link", { name: "Item 01" })).toBeInTheDocument();
    expect(lastList(server).get("limit")).toBe("26");
    expect(screen.getAllByRole("row")).toHaveLength(26); // header + 25
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("link", { name: "Item 26" })).toBeInTheDocument();
    expect(lastList(server).get("offset")).toBe("25");
    expect(screen.getByText("Page 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByRole("link", { name: "Item 01" })).toBeInTheDocument();
  });

  it("empty catalog and empty filter results say different things", async () => {
    renderProducts("/products", productServer([]));
    expect(await screen.findByRole("heading", { name: "No products yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add a product" })).toHaveAttribute("href", "/products/new");
  });

  it("no matches offers to clear the filters", async () => {
    renderProducts("/products?metal=platinum");
    expect(await screen.findByRole("heading", { name: "No products match these filters" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getAllByRole("button", { name: "Clear filters" })[0]);
    expect(await screen.findByRole("link", { name: "Halo Ring" })).toBeInTheDocument();
  });

  it("shows the error with a retry", async () => {
    const server = productServer([RING]);
    let recovered = false;
    server.override("GET /api/v1/products", () => (recovered ? undefined : jsonResponse(503, { detail: "Database is temporarily unavailable" })));
    renderProducts("/products", server);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Database is temporarily unavailable");
    recovered = true;
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: "Halo Ring" })).toBeInTheDocument();
  });

  it("publish and unpublish from the list", async () => {
    const { server } = renderProducts();
    const row = within((await screen.findByRole("link", { name: "Halo Ring" })).closest("tr")!);
    await userEvent.setup().click(row.getByRole("button", { name: "Unpublish Halo Ring" }));
    await waitFor(() => expect(server.last("PATCH", `/api/v1/products/${RING.id}`)?.body).toEqual({ is_published: false }));
    expect(await row.findByText("Draft")).toBeInTheDocument();
    expect(await screen.findByText("Product unpublished")).toBeInTheDocument();
  });
});

describe("delete", () => {
  it("a product on an invoice: shows the server's 409 and offers to unpublish instead", async () => {
    const server = productServer([RING, PENDANT], { onInvoice: new Set([RING.id]) });
    renderProducts("/products", server);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete Halo Ring" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete this product?" });
    expect(within(dialog).getByText("Halo Ring")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Delete product" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This product appears on 2 invoice line(s). Unpublish it instead (set is_published to false).",
    );
    await user.click(within(dialog).getByRole("button", { name: "Unpublish instead" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(server.last("PATCH", `/api/v1/products/${RING.id}`)?.body).toEqual({ is_published: false });
    expect(server.products.has(RING.id)).toBe(true);
  });

  it("deletes after confirmation; Cancel keeps it", async () => {
    const { server } = renderProducts();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete Moon Pendant" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(server.products.has(PENDANT.id)).toBe(true);

    await user.click(screen.getByRole("button", { name: "Delete Moon Pendant" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete product" }));
    await waitFor(() => expect(screen.queryByRole("link", { name: "Moon Pendant" })).not.toBeInTheDocument());
    expect(server.products.has(PENDANT.id)).toBe(false);
  });
});

describe("staff", () => {
  it("see the list read-only", async () => {
    renderProducts("/products", undefined, "staff");
    expect(await screen.findByRole("link", { name: "Halo Ring" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "New product" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^(Delete|Unpublish|Publish)/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View Halo Ring" })).toBeInTheDocument();
  });

  it("see a product read-only, with its images but no editing", async () => {
    renderProducts(`/products/${RING.id}`, undefined, "staff");
    expect(await screen.findByRole("heading", { level: 1, name: "Halo Ring" })).toBeInTheDocument();
    expect(screen.getByText(/read-only/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Halo Ring, image 1 of 1" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add images|Delete|Move|Make primary|Save/ })).not.toBeInTheDocument();
  });

  it("can't open the new-product page", async () => {
    renderProducts("/products/new", undefined, "staff");
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("You don't have access to this page");
  });
});
