import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fakeBackend, PAYMENTS_RESPONSE, renderApp, tokenFor } from "../test/harness";
import { isStripeOnboarding, leave } from "./api";
import { settingsServer } from "./testServer";

// Slice B2: Settings › Card payments.

afterEach(() => vi.restoreAllMocks());

const ACCOUNT = {
  account_id: "acct_1TestShop",
  charges_enabled: false,
  details_submitted: false,
  connected_at: "2026-10-27T10:00:00Z",
  disconnected_at: null,
};

function setup(payments = PAYMENTS_RESPONSE) {
  const server = settingsServer({ payments });
  renderApp({ path: "/settings", fetch: server.fetch, backend: fakeBackend(tokenFor("owner")) });
  return { server, user: userEvent.setup() };
}

const card = async () => within(await screen.findByRole("region", { name: "Card payments" }));

describe("card payments in settings", () => {
  it("not connected: Connect Stripe opens Stripe's onboarding", async () => {
    const leaving = vi.spyOn(leave, "to").mockImplementation(() => undefined);
    const { user } = setup();
    const section = await card();
    expect(section.getByText("Not connected")).toBeInTheDocument();
    expect(section.getByText("Stripe test mode")).toBeInTheDocument();
    await user.click(section.getByRole("button", { name: "Connect Stripe" }));
    await waitFor(() => expect(leaving).toHaveBeenCalledWith("https://connect.stripe.com/setup/s/acct_test_onboarding"));
  });

  it("setup not finished: continue where you left off", async () => {
    setup({ ...PAYMENTS_RESPONSE, account: ACCOUNT });
    const section = await card();
    expect(section.getByText("Setup not finished")).toBeInTheDocument();
    expect(section.getByText("acct_1TestShop")).toBeInTheDocument();
    expect(section.getByRole("button", { name: "Continue Stripe setup" })).toBeInTheDocument();
  });

  it("connected: card payments on, no button", async () => {
    setup({ ...PAYMENTS_RESPONSE, account: { ...ACCOUNT, charges_enabled: true, details_submitted: true }, card_payments: true });
    const section = await card();
    expect(section.getByText("Card payments on")).toBeInTheDocument();
    expect(section.queryByRole("button")).not.toBeInTheDocument();
  });

  it("a demo shop and a server without Stripe can't connect", async () => {
    setup({ ...PAYMENTS_RESPONSE, is_demo: true });
    expect((await card()).getByText(/uses the showcase's Stripe test account/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect Stripe" })).not.toBeInTheDocument();
  });

  it("no Stripe on the server", async () => {
    setup({ ...PAYMENTS_RESPONSE, stripe_configured: false });
    expect((await card()).getByText(/aren't set up on this server yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect Stripe" })).not.toBeInTheDocument();
  });

  it("only Stripe's onboarding pages are followed", () => {
    expect(isStripeOnboarding("https://connect.stripe.com/setup/s/x")).toBe(true);
    expect(isStripeOnboarding("https://connect.stripe.com.evil.example/")).toBe(false);
    expect(isStripeOnboarding("http://connect.stripe.com/setup")).toBe(false);
  });
});
