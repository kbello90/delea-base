import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { addLine, loadBag } from "./lib/bag";
import { isStripeCheckout, leave } from "./lib/api";
import { CARD_STORE, CHECKOUT_URL, checkoutServer, OPEN_STORE, orderFrom, priceCart, RING, SIZE_FIELD, TOKEN } from "./test/checkoutServer";
import { json, PRODUCTS, renderShop } from "./test/fakeApi";

// Slice B2: paying by card on Stripe's hosted Checkout.

const NECKLACE = PRODUCTS[0]; // $98

afterEach(() => vi.restoreAllMocks());

function seedNecklace() {
  localStorage.setItem(`delea-bag:v1:${location.host}`, JSON.stringify(addLine([], NECKLACE.id, 1, {})));
}

async function fillContactAndAddress(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("First name"), "Sarah");
  await user.type(screen.getByLabelText("Last name"), "Jenkins");
  await user.type(screen.getByLabelText("Email"), "sarah@example.com");
  await user.type(screen.getByLabelText("Street address"), "1 Main St");
  await user.type(screen.getByLabelText("City"), "Portland");
  await user.selectOptions(screen.getByLabelText("State"), "ME");
  await user.type(screen.getByLabelText("ZIP code"), "04101");
}

const cardOrder = orderFrom(
  { contact: { first_name: "Sarah" }, fulfillment: "ship", payment_choice: "card", ship_to: { city: "Portland", state: "ME" } },
  priceCart([{ product_id: RING.id, qty: 1, personalization: { [SIZE_FIELD]: "7" } }], "ship"),
);
const usd = (cents: number) => `$${(cents / 100).toFixed(2)}`;

describe("card at checkout", () => {
  it("is offered only when the shop takes cards", async () => {
    seedNecklace();
    renderShop("/checkout", checkoutServer({ store: OPEN_STORE }).fetch);
    await screen.findByLabelText("First name");
    expect(screen.queryByRole("radio", { name: /^Card/ })).not.toBeInTheDocument();
  });

  it("places the order, empties the bag and continues to Stripe's page", async () => {
    seedNecklace();
    const leaving = vi.spyOn(leave, "to").mockImplementation(() => undefined);
    const user = userEvent.setup();
    const server = checkoutServer({ store: CARD_STORE });
    renderShop("/checkout", server.fetch);
    await fillContactAndAddress(user);
    await user.click(screen.getByRole("radio", { name: /^Card/ }));
    expect(screen.getByText(/Your card details never reach us/)).toBeInTheDocument();
    expect(screen.getByText("Next, you pay on Stripe's secure page. Your order is placed first.")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing is charged online/)).not.toBeInTheDocument();
    const go = await screen.findByRole("button", { name: /^Continue to payment · \$/ });
    await user.click(go);
    await waitFor(() => expect(leaving).toHaveBeenCalledWith(CHECKOUT_URL));
    expect(server.orders).toHaveLength(1);
    expect(server.orders[0].payment_choice).toBe("card");
    expect(loadBag()).toEqual([]);
  });

  it("never follows a link that isn't Stripe's Checkout", () => {
    expect(isStripeCheckout(CHECKOUT_URL)).toBe(true);
    expect(isStripeCheckout("https://checkout.stripe.com.evil.example/x")).toBe(false);
    expect(isStripeCheckout("http://checkout.stripe.com/c/pay/x")).toBe(false);
    expect(isStripeCheckout("https://evil.example/?https://checkout.stripe.com/")).toBe(false);
    expect(isStripeCheckout(null)).toBe(false);
  });
});

describe("Pay now on the tracking page", () => {
  it("an unpaid card order: Pay now opens Stripe's page; no bank details are shown", async () => {
    const leaving = vi.spyOn(leave, "to").mockImplementation(() => undefined);
    const user = userEvent.setup();
    const server = checkoutServer({ store: CARD_STORE });
    server.setPlaced(cardOrder);
    renderShop(`/orders/${TOKEN}`, server.fetch);
    const pay = within(await screen.findByRole("region", { name: "Pay by card" }));
    expect(screen.queryByText(/Transfer to account 000/)).not.toBeInTheDocument();
    await user.click(pay.getByRole("button", { name: `Pay ${usd(cardOrder.total_cents)} by card` }));
    await waitFor(() => expect(leaving).toHaveBeenCalledWith(CHECKOUT_URL));
    expect(server.pays()).toBe(1);
  });

  it("says why when the payment page can't open", async () => {
    const user = userEvent.setup();
    const server = checkoutServer({
      store: CARD_STORE,
      onPay: () => json(502, { detail: "The card payment couldn't start. Please try again in a minute." }),
    });
    server.setPlaced(cardOrder);
    renderShop(`/orders/${TOKEN}`, server.fetch);
    await user.click(await screen.findByRole("button", { name: /by card$/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The card payment couldn't start. Please try again in a minute.");
  });

  it("a bank-transfer order may also be paid by card when the shop takes cards", async () => {
    const server = checkoutServer({ store: CARD_STORE });
    server.setPlaced({ ...cardOrder, payment_choice: "bank_transfer" });
    renderShop(`/orders/${TOKEN}`, server.fetch);
    const how = within(await screen.findByRole("region", { name: "How to pay" }));
    expect(how.getByText(/Transfer to account 000/)).toBeInTheDocument();
    expect(how.getByRole("button", { name: `Or pay ${usd(cardOrder.total_cents)} by card now` })).toBeInTheDocument();
  });

  it("a deposit: the rest can be paid by card; under 50 cents it is paid with the shop, no button", async () => {
    const server = checkoutServer({ store: CARD_STORE });
    server.setPlaced({ ...cardOrder, payment_choice: "bank_transfer", status: "deposit_paid", amount_paid_cents: 5000, balance_cents: cardOrder.total_cents - 5000 });
    const { unmount } = renderShop(`/orders/${TOKEN}`, server.fetch);
    const due = within(await screen.findByRole("region", { name: "Balance due" }));
    expect(due.getByRole("button", { name: `Pay the rest, ${usd(cardOrder.total_cents - 5000)}, by card` })).toBeInTheDocument();
    unmount();

    server.setPlaced({ ...cardOrder, status: "in_production", amount_paid_cents: cardOrder.total_cents - 30, balance_cents: 30 });
    renderShop(`/orders/${TOKEN}`, server.fetch);
    const small = await screen.findByRole("region", { name: "Balance due" });
    expect(small).toHaveTextContent("Pay the rest with the shop: $0.30.");
    expect(screen.queryByRole("button", { name: /card/i })).not.toBeInTheDocument();
  });

  it("back from Stripe: a confirming message until the webhook has recorded it, then Paid", async () => {
    const server = checkoutServer({ store: CARD_STORE });
    server.setPlaced(cardOrder);
    renderShop(`/orders/${TOKEN}?paid=1`, server.fetch);
    expect(await screen.findByText(/Your card payment is being confirmed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /by card/ })).not.toBeInTheDocument();
    server.setPlaced({ ...cardOrder, status: "paid", payment_instructions: null, amount_paid_cents: cardOrder.total_cents, balance_cents: 0 });
    expect(await screen.findByText("Paid", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByText(/being confirmed/)).not.toBeInTheDocument();
  });

  it("no card for this shop: a card order says to contact the shop", async () => {
    const server = checkoutServer({ store: OPEN_STORE });
    server.setPlaced(cardOrder);
    renderShop(`/orders/${TOKEN}`, server.fetch);
    expect(await screen.findByText(/Card payments aren't available right now/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /by card/ })).not.toBeInTheDocument();
  });
});
