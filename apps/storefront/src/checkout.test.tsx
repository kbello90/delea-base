import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { addLine, cartItems, lineKey, loadBag, MAX_QTY } from "./lib/bag";
import { nextAttempt, orderFingerprint } from "./lib/checkoutKey";
import { checkoutServer, ENGRAVING_FIELD, OPEN_STORE, orderFrom, priceCart, RING, SIZE_FIELD, TOKEN } from "./test/checkoutServer";
import { json, PRODUCTS, renderShop, STORE } from "./test/fakeApi";

const NECKLACE = PRODUCTS[0]; // $98, no personalization

function seedBag(lines: { productId: string; qty: number; personalization?: Record<string, string> }[]) {
  let bag: ReturnType<typeof loadBag> = [];
  for (const line of lines) bag = addLine(bag, line.productId, line.qty, line.personalization ?? {});
  localStorage.setItem(`delea-bag:v1:${location.host}`, JSON.stringify(bag));
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

const placeButton = () => screen.getByRole("button", { name: /place order/i });

// ---------------------------------------------------------------------------
// The bag's rules (no prices, ever)
// ---------------------------------------------------------------------------
describe("bag", () => {
  it("keeps the same piece with different answers as separate lines, and merges equal ones", () => {
    let bag = addLine([], RING.id, 1, { [SIZE_FIELD]: "7" });
    bag = addLine(bag, RING.id, 2, { [SIZE_FIELD]: " 7 " });
    bag = addLine(bag, RING.id, 1, { [SIZE_FIELD]: "8" });
    expect(bag.map((l) => l.qty)).toEqual([3, 1]);
    expect(lineKey(RING.id, { a: "1", b: "2" })).toBe(lineKey(RING.id, { b: "2", a: "1" }));
    expect(addLine([], NECKLACE.id, 500, {})[0].qty).toBe(MAX_QTY);
  });

  it("sends product, quantity and answers only: never a price", () => {
    const items = cartItems(addLine([], RING.id, 2, { [SIZE_FIELD]: "7" }));
    expect(items).toEqual([{ product_id: RING.id, qty: 2, personalization: { [SIZE_FIELD]: "7" } }]);
    expect(JSON.stringify(items)).not.toMatch(/price|total|cents/);
  });

  it("ignores a damaged or forged stored bag", () => {
    localStorage.setItem(`delea-bag:v1:${location.host}`, JSON.stringify([{ productId: "x", qty: 1000, personalization: {} }, { productId: 1 }, "junk"]));
    expect(loadBag()).toEqual([]);
    localStorage.setItem(`delea-bag:v1:${location.host}`, "{not json");
    expect(loadBag()).toEqual([]);
  });
});

describe("checkout key", () => {
  it("one key per attempt: reused for the same order, new when the order changes", () => {
    const order = { items: [{ product_id: "p", qty: 1 }], expected_total_cents: 100 };
    let n = 0;
    const key = () => `key-${++n}`;
    const first = nextAttempt(null, orderFingerprint(order), key);
    // The bot checks and the Turnstile token are not the order: a retry keeps the key.
    const retry = nextAttempt(first, orderFingerprint({ ...order, turnstile_token: "new", elapsed_ms: 9, website: "" }), key);
    expect(retry.key).toBe(first.key);
    const changed = nextAttempt(first, orderFingerprint({ ...order, expected_total_cents: 120 }), key);
    expect(changed.key).not.toBe(first.key);
    expect(orderFingerprint({ b: 1, a: { d: 1, c: 2 } })).toBe(orderFingerprint({ a: { c: 2, d: 1 }, b: 1 }));
  });
});

// ---------------------------------------------------------------------------
// Product page: personalization and adding
// ---------------------------------------------------------------------------
describe("product page", () => {
  it("asks the personalization questions; a required one blocks adding", async () => {
    const user = userEvent.setup();
    renderShop(`/products/${RING.id}`, checkoutServer().fetch);
    await screen.findByRole("heading", { level: 1, name: "Halo Ring" });
    expect(screen.getByLabelText(/Ring size/)).toHaveAttribute("aria-required", "true");
    expect(screen.getByLabelText(/Engraving/)).toHaveAttribute("maxLength", "10");

    await user.click(screen.getByRole("button", { name: "Add to bag" }));
    expect(await screen.findByText("Choose a ring size.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Ring size/)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText(/Ring size/)).toHaveFocus();
    expect(screen.getByRole("link", { name: "Bag, 0 items" })).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/Ring size/), "7");
    await user.type(screen.getByLabelText(/Engraving/), "K & J");
    await user.click(screen.getByRole("button", { name: "Add to bag" }));
    expect(await screen.findByText(/Added to your bag/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Bag, 1 item" })).toBeInTheDocument();
    expect(loadBag()[0].personalization).toEqual({ [SIZE_FIELD]: "7", [ENGRAVING_FIELD]: "K & J" });
  });

  it("a card adds a plain piece in one tap; a personalized one sends you to choose", async () => {
    const user = userEvent.setup();
    renderShop("/", checkoutServer().fetch);
    await user.click(await screen.findByRole("button", { name: `Add ${NECKLACE.name} to bag` }));
    expect(screen.getByRole("link", { name: "Bag, 1 item" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Choose options: Halo Ring" })).toHaveAttribute("href", `/products/${RING.id}`);
    expect(screen.getByRole("link", { name: "View piece: Engagement Ring" })).toBeInTheDocument(); // sold out
  });

  it("when the shop takes no orders: no bag, and the page says so", async () => {
    renderShop(`/products/${RING.id}`, checkoutServer({ store: { ...OPEN_STORE, checkout_enabled: false } }).fetch);
    expect(await screen.findByText(/Online ordering is temporarily unavailable/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add to bag" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Bag/ })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Bag page
// ---------------------------------------------------------------------------
describe("bag page", () => {
  it("shows the server's prices and the answers; quantity changes ask again", async () => {
    seedBag([{ productId: RING.id, qty: 1, personalization: { [SIZE_FIELD]: "7" } }]);
    const user = userEvent.setup();
    const server = checkoutServer();
    renderShop("/bag", server.fetch);
    expect(await screen.findByText("$145.00", { selector: ".bag__price" })).toBeInTheDocument();
    expect(screen.getByText("Ring size:")).toBeInTheDocument();
    expect(screen.getByText("Chosen at checkout")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "One more" }));
    expect(await screen.findByText("$290.00", { selector: ".bag__price" })).toBeInTheDocument();
    expect(server.quotes.at(-1)).toEqual({ items: [{ product_id: RING.id, qty: 2, personalization: { [SIZE_FIELD]: "7" } }], fulfillment: null });
  });

  it("a line that needs attention blocks checkout", async () => {
    seedBag([{ productId: RING.id, qty: 1 }]); // ring size missing
    renderShop("/bag", checkoutServer().fetch);
    expect(await screen.findByText(/Your choices for this piece need updating/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check out" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "Choose again" })).toHaveAttribute("href", `/products/${RING.id}`);
  });

  it("removing the last piece leaves an empty bag", async () => {
    seedBag([{ productId: NECKLACE.id, qty: 1 }]);
    const user = userEvent.setup();
    renderShop("/bag", checkoutServer().fetch);
    await user.click(await screen.findByRole("button", { name: `Remove ${NECKLACE.name} from your bag` }));
    expect(await screen.findByRole("heading", { name: "Your bag is empty" })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Checkout
// ---------------------------------------------------------------------------
describe("checkout", () => {
  it("ordering off: a clear message instead of the form, and no order is ever sent", async () => {
    seedBag([{ productId: NECKLACE.id, qty: 1 }]);
    const user = userEvent.setup();
    const server = checkoutServer({ store: { ...OPEN_STORE, checkout_enabled: false } });
    renderShop("/checkout", server.fetch);
    expect(await screen.findByText("Ordering is temporarily unavailable. Please try again later.")).toBeInTheDocument();
    expect(document.querySelector("form")).toBeNull();
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /place order/i })).not.toBeInTheDocument();
    await user.keyboard("{Enter}");
    await user.click(screen.getByRole("link", { name: "Back to your bag" }));
    expect(await screen.findByRole("heading", { level: 1, name: /bag/i })).toBeInTheDocument();
    expect(server.orders).toHaveLength(0);
  });

  it("checks the form before sending anything", async () => {
    seedBag([{ productId: NECKLACE.id, qty: 1 }]);
    const user = userEvent.setup();
    const server = checkoutServer();
    renderShop("/checkout", server.fetch);
    await waitFor(() => expect(placeButton()).toBeEnabled());
    await user.click(placeButton());
    expect(screen.getByText("Enter your first name.")).toBeInTheDocument();
    expect(screen.getByText("Enter a 5-digit ZIP code.")).toBeInTheDocument();
    expect(screen.getByLabelText("First name")).toHaveFocus();
    expect(server.orders).toHaveLength(0);
  });

  it("places the order with the server's total, a key, and the bot fields; never a price", async () => {
    seedBag([{ productId: RING.id, qty: 1, personalization: { [SIZE_FIELD]: "7" } }]);
    const user = userEvent.setup();
    const server = checkoutServer();
    renderShop("/checkout", server.fetch);
    const summary = within(await screen.findByRole("complementary", { name: "Order summary" }));
    expect(await summary.findByText("$162.60")).toBeInTheDocument(); // 145 + 6 shipping + 11.60 tax
    expect(summary.getByText("Ring size:")).toBeInTheDocument();
    await fillContactAndAddress(user);
    await user.click(placeButton());

    expect(await screen.findByRole("heading", { name: "Thank you, Sarah" })).toBeInTheDocument();
    expect(server.orders).toHaveLength(1);
    const body = server.orders[0];
    expect(Object.keys(body).sort()).toEqual(
      ["checkout_key", "contact", "elapsed_ms", "expected_total_cents", "fulfillment", "items", "payment_choice", "ship_to", "website"].sort(),
    );
    expect(body.expected_total_cents).toBe(16260);
    expect(body.checkout_key).toMatch(/^[0-9a-f-]{36}$/);
    expect(body.website).toBe("");
    expect(typeof body.elapsed_ms).toBe("number");
    expect(body.items).toEqual([{ product_id: RING.id, qty: 1, personalization: { [SIZE_FIELD]: "7" } }]);
    expect(body.ship_to).toEqual({ line1: "1 Main St", city: "Portland", state: "ME", postal_code: "04101" });
    expect(JSON.stringify(body.items)).not.toMatch(/price|total/);
    expect(loadBag()).toEqual([]); // the bag is emptied
  });

  it("a double click sends one request", async () => {
    seedBag([{ productId: NECKLACE.id, qty: 1 }]);
    const user = userEvent.setup();
    const server = checkoutServer({ onOrder: () => new Promise((resolve) => setTimeout(() => resolve(undefined), 300)) });
    renderShop("/checkout", server.fetch);
    await fillContactAndAddress(user);
    await waitFor(() => expect(placeButton()).toBeEnabled());
    await user.dblClick(placeButton());
    expect(await screen.findByRole("heading", { name: "Thank you, Sarah" })).toBeInTheDocument();
    expect(server.orders).toHaveLength(1);
  });

  it("a retry after a lost connection reuses the checkout key", async () => {
    seedBag([{ productId: NECKLACE.id, qty: 1 }]);
    const user = userEvent.setup();
    const server = checkoutServer({
      onOrder: (_body, n) => {
        if (n === 1) throw new TypeError("Failed to fetch");
        return undefined;
      },
    });
    renderShop("/checkout", server.fetch);
    await fillContactAndAddress(user);
    await waitFor(() => expect(placeButton()).toBeEnabled());
    await user.click(placeButton());
    expect(await screen.findByText(/We couldn't reach the shop/)).toBeInTheDocument();
    await user.click(placeButton());
    expect(await screen.findByRole("heading", { name: "Thank you, Sarah" })).toBeInTheDocument();
    expect(server.orders).toHaveLength(2);
    expect(server.orders[1].checkout_key).toBe(server.orders[0].checkout_key);
  });

  it("the fill time runs from when the checkout page loaded", async () => {
    let now = 1_000_000;
    const clock = vi.spyOn(Date, "now").mockImplementation(() => now);
    try {
      seedBag([{ productId: NECKLACE.id, qty: 1 }]);
      const user = userEvent.setup();
      const server = checkoutServer();
      renderShop("/checkout", server.fetch);
      await waitFor(() => expect(placeButton()).toBeEnabled());
      now += 4_000; // reading the summary before typing anything
      await fillContactAndAddress(user);
      now += 1_000;
      await user.click(placeButton());
      await waitFor(() => expect(server.orders).toHaveLength(1));
      expect(server.orders[0].elapsed_ms).toBe(5_000);
    } finally {
      clock.mockRestore();
    }
  });

  it("placed too quickly: a friendly message, and placing it again reuses the checkout key", async () => {
    seedBag([{ productId: NECKLACE.id, qty: 1 }]);
    const user = userEvent.setup();
    const server = checkoutServer({
      onOrder: (_body, n) => (n === 1 ? json(422, { detail: "Your order didn't go through.", reason: "try_again" }) : undefined),
    });
    renderShop("/checkout", server.fetch);
    await fillContactAndAddress(user);
    await waitFor(() => expect(placeButton()).toBeEnabled());
    await user.click(placeButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("Sorry, your order didn't go through. Please check your details and place it again.");
    await user.click(placeButton());
    expect(await screen.findByRole("heading", { name: "Thank you, Sarah" })).toBeInTheDocument();
    expect(server.orders).toHaveLength(2);
    expect(server.orders[1].checkout_key).toBe(server.orders[0].checkout_key);
  });

  it("a changed price: shows the new total and asks to confirm, then orders with a new key", async () => {
    seedBag([{ productId: NECKLACE.id, qty: 1 }]);
    const user = userEvent.setup();
    const newQuote = { ...priceCart([{ product_id: NECKLACE.id, qty: 1 }], "ship"), total_cents: 12000 };
    const server = checkoutServer({
      onOrder: (_body, n) => (n === 1 ? json(409, { detail: "Prices changed since you opened checkout. Please review your order.", reason: "price_changed", quote: newQuote }) : undefined),
    });
    renderShop("/checkout", server.fetch);
    await fillContactAndAddress(user);
    await waitFor(() => expect(placeButton()).toBeEnabled());
    await user.click(placeButton());
    expect(await screen.findByText(/Prices changed since you opened checkout/)).toBeInTheDocument();
    expect(screen.getByText(/The new total is/)).toHaveTextContent("$120.00");
    const confirm = screen.getByRole("button", { name: /Confirm new total and place order · \$120\.00/ });
    expect(server.orders).toHaveLength(1);
    await user.click(confirm);
    await waitFor(() => expect(server.orders).toHaveLength(2));
    expect(server.orders[1].expected_total_cents).toBe(12000);
    expect(server.orders[1].checkout_key).not.toBe(server.orders[0].checkout_key);
  });

  it.each([
    [429, { detail: "Too many requests. Please wait a few minutes and try again." }, "Too many attempts from your connection. Please wait a few minutes, then try again."],
    [422, { detail: "The security check didn't pass. Please try again." }, "The security check didn't pass. Please try again."],
    [503, { detail: "Online ordering is unavailable right now." }, "Ordering is temporarily unavailable. Please try again later."],
    [409, { detail: "This checkout was already used for a different order." }, "This checkout was already used for a different order."],
  ])("a %i answer gets a plain message", async (status, body, message) => {
    seedBag([{ productId: NECKLACE.id, qty: 1 }]);
    const user = userEvent.setup();
    renderShop("/checkout", checkoutServer({ onOrder: () => json(status, body) }).fetch);
    await fillContactAndAddress(user);
    await waitFor(() => expect(placeButton()).toBeEnabled());
    await user.click(placeButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });

  it("unavailable pieces: says so and links back to the bag", async () => {
    seedBag([{ productId: NECKLACE.id, qty: 1 }]);
    const user = userEvent.setup();
    const quote = priceCart([{ product_id: NECKLACE.id, qty: 1 }], "ship");
    quote.lines[0].problem = "unavailable";
    quote.has_problems = true;
    renderShop("/checkout", checkoutServer({ onOrder: () => json(409, { detail: "Some items are no longer available or need your choices.", reason: "unavailable", quote }) }).fetch);
    await fillContactAndAddress(user);
    await waitFor(() => expect(placeButton()).toBeEnabled());
    await user.click(placeButton());
    expect(await screen.findByText("Some items are no longer available or need your choices.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review your bag" })).toHaveAttribute("href", "/bag");
  });

  it("paying on pickup is offered only for pickup, and pickup needs no address", async () => {
    seedBag([{ productId: NECKLACE.id, qty: 1 }]);
    const user = userEvent.setup();
    const server = checkoutServer();
    renderShop("/checkout", server.fetch);
    await screen.findByLabelText("First name");
    expect(screen.queryByRole("radio", { name: /Pay when you pick up/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /Pick up/ }));
    expect(screen.queryByLabelText("Street address")).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /Pay when you pick up/ }));
    await user.type(screen.getByLabelText("First name"), "Priya");
    await user.type(screen.getByLabelText("Last name"), "Nair");
    await user.type(screen.getByLabelText("Email"), "priya@example.com");
    await waitFor(() => expect(placeButton()).toHaveTextContent("$105.84"));
    await user.click(placeButton());
    await screen.findByRole("heading", { name: "Thank you, Priya" });
    expect(server.orders[0]).toMatchObject({ fulfillment: "pickup", payment_choice: "pay_on_pickup", expected_total_cents: 10584 });
    expect(server.orders[0]).not.toHaveProperty("ship_to");
  });

  it("every field has a label; choices are real radio buttons", async () => {
    seedBag([{ productId: NECKLACE.id, qty: 1 }]);
    renderShop("/checkout", checkoutServer().fetch);
    await screen.findByLabelText("First name");
    for (const input of document.querySelectorAll(".checkout__form input:not([type=radio]):not(#checkout-website), .checkout__form select, .checkout__form textarea")) {
      expect(document.querySelector(`label[for="${input.id}"]`), input.id).not.toBeNull();
    }
    expect(screen.getAllByRole("radio").length).toBeGreaterThanOrEqual(3);
    // The hidden field is out of reach for people and assistive tech.
    expect(document.getElementById("checkout-website")).toHaveAttribute("tabIndex", "-1");
    expect(document.querySelector(".hp")).toHaveAttribute("aria-hidden", "true");
  });
});

// ---------------------------------------------------------------------------
// Confirmation and tracking
// ---------------------------------------------------------------------------
describe("tracking page", () => {
  const order = orderFrom(
    { contact: { first_name: "Sarah" }, fulfillment: "ship", payment_choice: "bank_transfer", ship_to: { city: "Portland", state: "ME" } },
    priceCart([{ product_id: RING.id, qty: 1, personalization: { [SIZE_FIELD]: "7" } }], "ship"),
  );

  it("shows the W- reference, first name and city only, the answers and how to pay", async () => {
    const server = checkoutServer();
    server.setPlaced(order);
    renderShop(`/orders/${TOKEN}`, server.fetch);
    expect(await screen.findByRole("heading", { name: "Order W-7K3P9QAB" })).toBeInTheDocument();
    expect(screen.getByText("Waiting for payment")).toBeInTheDocument();
    expect(screen.getByText(/Transfer to account 000/)).toBeInTheDocument();
    expect(screen.getByText("Shipping to Portland.")).toBeInTheDocument();
    expect(screen.getByText("Ring size:")).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/INV-|QTE-/);
    expect(text).not.toContain("ME"); // city only, not the state
    expect(document.querySelector('meta[name="referrer"]')).toHaveAttribute("content", "no-referrer");
  });

  it("an unknown link is a plain not-found", async () => {
    renderShop(`/orders/${TOKEN}`, checkoutServer().fetch);
    expect(await screen.findByRole("heading", { name: "We couldn't find that order" })).toBeInTheDocument();
  });

  it("a paid order no longer shows payment instructions", async () => {
    const server = checkoutServer();
    server.setPlaced({ ...order, status: "paid", payment_instructions: null });
    renderShop(`/orders/${TOKEN}`, server.fetch);
    expect(await screen.findByText("Paid")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "How to pay" })).not.toBeInTheDocument();
  });

  const usd = (cents: number) => `$${(cents / 100).toFixed(2)}`;

  it("a deposit shows what was paid, the balance and how to pay the rest", async () => {
    const server = checkoutServer();
    const balance = order.total_cents - 5000;
    server.setPlaced({ ...order, status: "deposit_paid", amount_paid_cents: 5000, balance_cents: balance });
    renderShop(`/orders/${TOKEN}`, server.fetch);
    expect(await screen.findByText("Deposit received")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(`Thank you, your deposit arrived. Balance due ${usd(balance)}.`);
    const due = screen.getByRole("region", { name: "Balance due" });
    expect(due).toHaveTextContent(`Paid so far $50.00 of ${usd(order.total_cents)}.`);
    expect(due).toHaveTextContent(/Transfer to account 000/);
    expect(screen.queryByRole("heading", { name: "How to pay" })).not.toBeInTheDocument();
    expect(screen.getByText("Paid so far").nextSibling).toHaveTextContent("$50.00");
  });

  it("in production on a deposit: the status says what is still due", async () => {
    const server = checkoutServer();
    const balance = order.total_cents - 5000;
    server.setPlaced({ ...order, status: "in_production", amount_paid_cents: 5000, balance_cents: balance });
    renderShop(`/orders/${TOKEN}`, server.fetch);
    expect(await screen.findByText("In production")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(`Your piece is being made. Balance due ${usd(balance)}.`);
  });

  it("a balance under 50 cents is paid with the shop, with no Pay button", async () => {
    const server = checkoutServer();
    server.setPlaced({ ...order, status: "in_production", amount_paid_cents: order.total_cents - 30, balance_cents: 30 });
    renderShop(`/orders/${TOKEN}`, server.fetch);
    const due = await screen.findByRole("region", { name: "Balance due" });
    expect(due).toHaveTextContent("Pay the rest with the shop: $0.30.");
    expect(due).not.toHaveTextContent(/Transfer to account 000/);
    expect(screen.queryByRole("button", { name: /pay/i })).not.toBeInTheDocument();
  });

  it("paid in full shows no balance", async () => {
    const server = checkoutServer();
    server.setPlaced({ ...order, status: "paid", payment_instructions: null, amount_paid_cents: order.total_cents, balance_cents: 0 });
    renderShop(`/orders/${TOKEN}`, server.fetch);
    expect(await screen.findByText("Paid")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Balance due" })).not.toBeInTheDocument();
    expect(screen.queryByText("Paid so far")).not.toBeInTheDocument();
  });
});

describe("the shop without checkout", () => {
  it("keeps A1's behaviour", async () => {
    renderShop("/", checkoutServer({ store: STORE }).fetch);
    await screen.findByRole("heading", { level: 2, name: "The collection" });
    expect(screen.queryByRole("link", { name: /^Bag/ })).not.toBeInTheDocument();
  });
});
