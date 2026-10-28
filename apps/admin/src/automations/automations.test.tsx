import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { EMAILS_RESPONSE, fakeApi, fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";

// Website slice, separate page: every automatic email, its trigger and whether it is on. Read only.

function setup(emails = EMAILS_RESPONSE, role: "owner" | "staff" | "cpa" = "owner") {
  const fetch = fakeApi({ "/api/v1/settings/emails": () => jsonResponse(200, emails) });
  renderApp({ path: "/automations", fetch, backend: fakeBackend(tokenFor(role)) });
  return { fetch, user: userEvent.setup() };
}

describe("automations page", () => {
  it("lists each email and alert with its trigger and whether it is on", async () => {
    setup({
      ...EMAILS_RESPONSE,
      alerts: { ...EMAILS_RESPONSE.alerts, low_stock: false },
      customer: { ...EMAILS_RESPONSE.customer, shipped: { enabled: false, intro: null } },
    });
    const table = within(await screen.findByRole("table", { name: "Automatic emails" }));
    const row = (name: string) => within(table.getByRole("row", { name: new RegExp(`^${name}`) }));
    expect(table.getAllByRole("row")).toHaveLength(9); // header + 8
    expect(row("New website order").getByText("On")).toBeInTheDocument();
    expect(row("Low stock").getByText("Off")).toBeInTheDocument();
    expect(row("Shipped").getByText("Off")).toBeInTheDocument();
    expect(row("Deposit received").getByText("A deposit is received on a website order.")).toBeInTheDocument();
    expect(row("In production").getByText("Production starts on a website order (once per order).")).toBeInTheDocument();
    expect(row("Order received").getByText("The shopper")).toBeInTheDocument();
    expect(row("Balance due").getByText("You")).toBeInTheDocument();
  });

  it("is read only: no switches, a link to Settings › Emails", async () => {
    const { fetch, user } = setup();
    await screen.findByRole("table", { name: "Automatic emails" });
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "Change in Settings › Emails" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    const writes = fetch.mock.calls.filter(([r]) => (r as Request).method !== "GET");
    expect(writes).toEqual([]);
  });

  it.each(["staff", "cpa"] as const)("is not shown to %s", async (role) => {
    setup(EMAILS_RESPONSE, role);
    expect(await screen.findByRole("navigation", { name: "Main" })).not.toHaveTextContent("Automations");
    expect(screen.queryByRole("table", { name: "Automatic emails" })).not.toBeInTheDocument();
  });
});
