import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";
import { makeMaterial, materialServer } from "./testServer";

const WIRE = makeMaterial({ name: "Silver wire", unit: "g", unit_cost_cents: 125, qty_on_hand: "1.50", reorder_threshold: "5.00" });
const BOXES = makeMaterial({ name: "Gift boxes", unit: "pcs", unit_cost_cents: 90, qty_on_hand: "40.00", reorder_threshold: "10.00" });

function renderMaterials(path = "/materials", server = materialServer([WIRE, BOXES]), role: "owner" | "staff" = "owner") {
  const utils = renderApp({ path, fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { ...utils, server };
}

const lastList = (server: ReturnType<typeof materialServer>) => server.last("GET", "/api/v1/materials")!.query;
const rowOf = async (name: string) => within((await screen.findByRole("cell", { name })).closest("tr")!);

describe("materials list", () => {
  it("shows each material with unit, cost, quantities and the low-stock flag", async () => {
    renderMaterials();
    const wire = await rowOf("Silver wire");
    expect(wire.getByText("g", { selector: "td" })).toBeInTheDocument();
    expect(await wire.findByText("$1.25")).toBeInTheDocument();
    expect(wire.getByText("1.5")).toBeInTheDocument();
    expect(wire.getByText("5")).toBeInTheDocument();
    expect(wire.getByText("Low stock")).toBeInTheDocument();

    const boxes = await rowOf("Gift boxes");
    expect(boxes.getByText("40")).toBeInTheDocument();
    expect(boxes.queryByText("Low stock")).not.toBeInTheDocument();
  });

  it("filters by search and low stock (in the URL and the request)", async () => {
    const { server } = renderMaterials();
    await rowOf("Silver wire");
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Search"), "box");
    await waitFor(() => expect(lastList(server).get("q")).toBe("box"));
    await waitFor(() => expect(screen.queryByRole("cell", { name: "Silver wire" })).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await user.click(screen.getByLabelText("Low stock only"));
    await waitFor(() => expect(lastList(server).get("low_stock")).toBe("true"));
    expect(await screen.findByRole("cell", { name: "Silver wire" })).toBeInTheDocument();
    expect(screen.queryByRole("cell", { name: "Gift boxes" })).not.toBeInTheDocument();
  });

  it("the dashboard's low-stock link opens the filtered list", async () => {
    const { server } = renderMaterials("/materials?low_stock=true");
    expect(await screen.findByRole("cell", { name: "Silver wire" })).toBeInTheDocument();
    expect(screen.getByLabelText("Low stock only")).toBeChecked();
    expect(lastList(server).get("low_stock")).toBe("true");
    expect(screen.queryByRole("cell", { name: "Gift boxes" })).not.toBeInTheDocument();
  });

  it("pages through results 25 at a time", async () => {
    const many = Array.from({ length: 27 }, (_, i) => makeMaterial({ name: `Item ${String(i + 1).padStart(2, "0")}` }));
    const { server } = renderMaterials("/materials", materialServer(many));
    expect(await screen.findByRole("cell", { name: "Item 01" })).toBeInTheDocument();
    expect(lastList(server).get("limit")).toBe("26");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("cell", { name: "Item 26" })).toBeInTheDocument();
    expect(lastList(server).get("offset")).toBe("25");
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("empty list and no matches say different things", async () => {
    renderMaterials("/materials", materialServer([]));
    expect(await screen.findByRole("heading", { name: "No materials yet" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a material" })).toBeInTheDocument();
  });

  it("no matches offers to clear the filters", async () => {
    renderMaterials("/materials?q=zzz");
    expect(await screen.findByRole("heading", { name: "No materials match these filters" })).toBeInTheDocument();
  });

  it("shows the error with a retry", async () => {
    const server = materialServer([WIRE]);
    let recovered = false;
    server.override("GET /api/v1/materials", () => (recovered ? undefined : jsonResponse(503, { detail: "Database is temporarily unavailable" })));
    renderMaterials("/materials", server);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Database is temporarily unavailable");
    recovered = true;
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("cell", { name: "Silver wire" })).toBeInTheDocument();
  });
});

describe("create and edit", () => {
  it("adds a material; the list refreshes", async () => {
    const { server } = renderMaterials();
    await rowOf("Silver wire");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "New material" }));
    const dialog = within(await screen.findByRole("dialog", { name: "New material" }));

    await user.click(dialog.getByRole("button", { name: "Add material" }));
    expect(dialog.getByText("Enter a material name.")).toBeInTheDocument();
    expect(dialog.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
    expect(server.last("POST", "/api/v1/materials")).toBeUndefined();

    await user.type(dialog.getByLabelText("Name"), "Pearls");
    await user.type(dialog.getByLabelText("Unit"), "pcs");
    await user.type(dialog.getByLabelText("Cost per unit"), "4.20");
    await user.clear(dialog.getByLabelText("On hand"));
    await user.type(dialog.getByLabelText("On hand"), "12");
    await user.clear(dialog.getByLabelText("Reorder threshold"));
    await user.type(dialog.getByLabelText("Reorder threshold"), "20");
    await user.click(dialog.getByRole("button", { name: "Add material" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(server.last("POST", "/api/v1/materials")?.body).toEqual({
      name: "Pearls",
      unit: "pcs",
      unit_cost_cents: 420,
      qty_on_hand: "12.00",
      reorder_threshold: "20.00",
    });
    expect((await rowOf("Pearls")).getByText("Low stock")).toBeInTheDocument();
    expect(await screen.findByText("Material added")).toBeInTheDocument();
  });

  async function submitNew(name: string) {
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "New material" }));
    const dialog = within(await screen.findByRole("dialog"));
    await user.type(dialog.getByLabelText("Name"), name);
    await user.type(dialog.getByLabelText("Unit"), "pcs");
    await user.type(dialog.getByLabelText("Cost per unit"), "1");
    await user.click(dialog.getByRole("button", { name: "Add material" }));
    return dialog;
  }

  it("a duplicate-name 409 is shown on the name field only", async () => {
    renderMaterials();
    await rowOf("Silver wire");
    const dialog = await submitNew("Gift boxes");
    expect(await dialog.findByText("Another material already has this name.")).toBeInTheDocument();
    expect(dialog.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
    expect(dialog.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("any other 409 is a form error with the server's message, not a name error", async () => {
    const server = materialServer([WIRE, BOXES]);
    server.override("POST /api/v1/materials", () =>
      jsonResponse(409, { detail: "The request conflicts with a business rule" }),
    );
    renderMaterials("/materials", server);
    await rowOf("Silver wire");
    const dialog = await submitNew("Pearls");
    expect(await dialog.findByRole("alert")).toHaveTextContent("The request conflicts with a business rule");
    expect(dialog.queryByText("Another material already has this name.")).not.toBeInTheDocument();
    expect(dialog.getByLabelText("Name")).toHaveAttribute("aria-invalid", "false");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("edits a material, sending only what changed", async () => {
    const { server } = renderMaterials();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit Silver wire" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Edit Silver wire" }));
    expect(dialog.getByLabelText("On hand")).toHaveValue("1.5");
    expect(dialog.getByLabelText("Cost per unit")).toHaveValue("1.25");

    await user.clear(dialog.getByLabelText("On hand"));
    await user.type(dialog.getByLabelText("On hand"), "8");
    await user.click(dialog.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(server.last("PATCH", `/api/v1/materials/${WIRE.id}`)?.body).toEqual({ qty_on_hand: "8.00" });
    const wire = await rowOf("Silver wire");
    await waitFor(() => expect(wire.getByText("8")).toBeInTheDocument());
    expect(wire.queryByText("Low stock")).not.toBeInTheDocument();
  });

  it("saving with no changes sends nothing", async () => {
    const { server } = renderMaterials();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit Gift boxes" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("No changes to save")).toBeInTheDocument();
    expect(server.last("PATCH", `/api/v1/materials/${BOXES.id}`)).toBeUndefined();
  });
});

describe("delete", () => {
  it("a material used by products: shows the server's 409 and keeps it", async () => {
    const server = materialServer([WIRE, BOXES], { usedBy: new Set([WIRE.id]) });
    renderMaterials("/materials", server);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete Silver wire" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Delete this material?" }));
    await user.click(dialog.getByRole("button", { name: "Delete material" }));
    expect(await dialog.findByRole("alert")).toHaveTextContent(
      "This material is used by 2 product(s); remove it from those products first.",
    );
    expect(server.materials.has(WIRE.id)).toBe(true);
  });

  it("deletes after confirmation", async () => {
    const { server } = renderMaterials();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete Gift boxes" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete material" }));
    await waitFor(() => expect(screen.queryByRole("cell", { name: "Gift boxes" })).not.toBeInTheDocument());
    expect(server.materials.has(BOXES.id)).toBe(false);
  });
});

describe("staff", () => {
  it("see the list read-only", async () => {
    renderMaterials("/materials", undefined, "staff");
    expect(await screen.findByRole("cell", { name: "Silver wire" })).toBeInTheDocument();
    expect(screen.getByText("You can view materials; only the owner can change them.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^(New material|Edit|Delete)/ })).not.toBeInTheDocument();
  });
});
