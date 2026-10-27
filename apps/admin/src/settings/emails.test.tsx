import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { EMAILS_RESPONSE, fakeApi, fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";
import { settingsServer } from "./testServer";

// Slice D1: Settings › Emails, and the reply-to confirmation page.

function setup(emails = EMAILS_RESPONSE) {
  const server = settingsServer({ emails });
  renderApp({ path: "/settings", fetch: server.fetch, backend: fakeBackend(tokenFor("owner")) });
  return { server, user: userEvent.setup() };
}

const card = async () => within(await screen.findByRole("region", { name: "Emails" }));

describe("settings › emails", () => {
  it("says who sends and where replies go", async () => {
    setup();
    const section = await card();
    expect(section.getByText("Local mail catcher (development)")).toBeInTheDocument();
    expect(section.getByText("DELEA <notifications@mail.deleajewelry.com>")).toBeInTheDocument();
    expect(section.getByText(/Not set: replies go nowhere useful/)).toBeInTheDocument();
    expect(section.getByText("No emails yet.")).toBeInTheDocument();
  });

  it("asks for a reply-to address: checked first, then a confirmation link, shown as waiting", async () => {
    const { server, user } = setup();
    const section = await card();
    await user.type(section.getByLabelText("Email for replies"), "not-an-email");
    await user.click(section.getByRole("button", { name: "Send confirmation link" }));
    expect(section.getByText("Enter an email address like you@example.com.")).toBeInTheDocument();
    expect(server.calls.some((c) => c.path.endsWith("/reply-to"))).toBe(false);
    await user.clear(section.getByLabelText("Email for replies"));
    await user.type(section.getByLabelText("Email for replies"), "shop@gmail.com");
    await user.click(section.getByRole("button", { name: "Send confirmation link" }));
    expect(await screen.findByText("Confirmation link sent to shop@gmail.com")).toBeInTheDocument();
    expect(section.getByText(/Waiting for confirmation: shop@gmail.com/)).toBeInTheDocument();
    expect(server.calls.find((c) => c.path.endsWith("/reply-to"))!.body).toEqual({ email: "shop@gmail.com" });
  });

  it("switches an alert off", async () => {
    const { server, user } = setup();
    const section = await card();
    await user.click(section.getByRole("checkbox", { name: /^Low stock/ }));
    await user.click(section.getByRole("button", { name: "Save alerts" }));
    expect(await screen.findByText("Email alerts saved")).toBeInTheDocument();
    expect(server.calls.find((c) => c.method === "PATCH" && c.path === "/api/v1/settings/emails")!.body).toEqual({
      alerts: { new_order: true, low_stock: false, balance_due: true },
    });
  });

  it("sends a test to the owner's own email", async () => {
    const { user } = setup();
    const section = await card();
    expect(section.getByText("It goes to the email you sign in with, nowhere else.")).toBeInTheDocument();
    await user.click(section.getByRole("button", { name: "Send a test to myself" }));
    expect(await screen.findByText("Test email on its way to owner@shop.com")).toBeInTheDocument();
  });

  it("shows delays and the latest emails, including those not sent and why", async () => {
    setup({
      ...EMAILS_RESPONSE,
      delayed: true,
      reply_to: "shop@gmail.com",
      recent: [
        { created_at: "2026-10-27T15:00:00Z", kind: "owner_alert", template: "new_order", recipient: "owner@shop.com", subject: "New website order W-1", status: "sent", last_error: null },
        { created_at: "2026-10-27T14:00:00Z", kind: "customer", template: "x", recipient: "x@example.com", subject: "Hi", status: "suppressed", last_error: "demo shop: only its own owners get email" },
      ],
    });
    const section = await card();
    expect(section.getByRole("status")).toHaveTextContent("Emails are delayed");
    expect(section.getByText("shop@gmail.com")).toBeInTheDocument();
    const table = within(section.getByRole("table", { name: "Latest emails" }));
    expect(table.getByText("Sent")).toBeInTheDocument();
    expect(table.getByText("Not sent")).toBeInTheDocument();
    expect(table.getByText(/demo shop: only its own owners get email/)).toBeInTheDocument();
  });
});

describe("the reply-to confirmation page", () => {
  it("confirms only on a click, then says so", async () => {
    const posts: unknown[] = [];
    const fetch = fakeApi({
      "/api/v1/email/confirm-reply-to": async (request) => {
        posts.push(await request.json());
        return jsonResponse(200, { shop: "Aurora Gems" });
      },
    });
    renderApp({ path: "/confirm-reply-to?token=abcdefghijklmnopqrstuvwxyz", fetch, backend: fakeBackend(null) });
    const button = await screen.findByRole("button", { name: "Confirm this address" });
    expect(posts).toEqual([]); // opening the link alone confirms nothing
    await userEvent.setup().click(button);
    expect(await screen.findByRole("status")).toHaveTextContent("Replies to Aurora Gems");
    expect(posts).toEqual([{ token: "abcdefghijklmnopqrstuvwxyz" }]);
  });

  it("an expired or used link says so", async () => {
    const fetch = fakeApi({ "/api/v1/email/confirm-reply-to": () => jsonResponse(400, { detail: "This link has expired or was already used." }) });
    renderApp({ path: "/confirm-reply-to?token=abcdefghijklmnopqrstuvwxyz", fetch, backend: fakeBackend(null) });
    await userEvent.setup().click(await screen.findByRole("button", { name: "Confirm this address" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("This link has expired or was already used."));
  });
});
