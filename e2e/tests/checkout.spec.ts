import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { BACKEND, E2E_STRIPE_WEBHOOK_SECRET, PORTS, PYTHON } from "../playwright.config";

interface Shop {
  tenant_id: string;
  origin: string;
  owner_email: string;
  owner_password: string;
  ring_id: string;
}

const shop = (): Shop => JSON.parse(readFileSync(new URL("../.shop.json", import.meta.url), "utf8")) as Shop;
const shots = (name: string) => `screenshots/${name}.png`;

/**
 * Two pictures per screen: what the phone shows (sticky header and pay bar where a shopper
 * sees them), and the whole page with those two flattened (a full-page capture would
 * otherwise paint them in the middle of the page).
 */
async function capture(page: Page, name: string) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: shots(`${name}`) });
  const flat = await page.addStyleTag({ content: ".site-header, .pay-bar { position: static !important; }" });
  await page.screenshot({ path: shots(`${name}-full`), fullPage: true });
  await flat.evaluate((el) => (el as Element).remove());
}

/** Waits for Turnstile's test widget to hand over its token. */
async function turnstileToken(page: Page) {
  await expect(page.locator('input[name="cf-turnstile-response"]')).toHaveValue(/.+/, { timeout: 30_000 });
}

test("a shopper orders a personalized ring; the owner sees it in the admin", async ({ browser }) => {
  const s = shop();

  // ---- The storefront, on a phone ----
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const page = await phone.newPage();
  await page.goto(s.origin);
  await expect(page.getByRole("heading", { level: 1, name: "Handcrafted pieces in 14K gold and 925 silver" })).toBeVisible();
  await capture(page, "1-storefront-phone");

  await page.getByRole("link", { name: "Choose options: Halo Ring" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Halo Ring" })).toBeVisible();
  // A required question blocks adding.
  await page.getByRole("button", { name: "Add to bag" }).click();
  await expect(page.getByText("Choose a ring size.")).toBeVisible();
  await page.getByLabel(/Ring size/).selectOption("7");
  await page.getByLabel(/Engraving/).fill("K & J");
  await page.getByRole("button", { name: "Add to bag" }).click();
  await expect(page.getByRole("link", { name: "Bag, 1 item" })).toBeVisible();

  await page.getByRole("link", { name: "Bag, 1 item" }).click();
  await expect(page.getByText("Ring size:")).toBeVisible();
  await page.getByRole("button", { name: "Check out" }).click();
  const checkoutOpened = Date.now();

  await page.getByLabel("First name").fill("Sarah");
  await page.getByLabel("Last name").fill("Jenkins");
  await page.getByLabel("Email").fill("sarah@example.com");
  await page.getByLabel("Phone (for delivery updates, optional)").fill("(555) 010-0142");
  await page.getByLabel("Street address").fill("1 Main St");
  await page.getByLabel("City").fill("Portland");
  await page.getByLabel("State").selectOption("ME");
  await page.getByLabel("ZIP code").fill("04101");
  await turnstileToken(page);
  const summary = page.getByRole("complementary", { name: "Order summary" });
  await expect(summary.getByText("$162.60")).toBeVisible(); // 145 + 6 shipping + 11.60 tax
  await capture(page, "2-checkout-phone");

  // A person takes longer than the API's minimum fill time (2 s from loading checkout); a robot this fast is refused.
  await page.waitForTimeout(Math.max(0, 2_500 - (Date.now() - checkoutOpened)));
  await page.getByRole("button", { name: /Place order · \$162\.60/ }).click();
  await expect(page.getByRole("heading", { name: "Thank you, Sarah" })).toBeVisible();
  const ref = (await page.locator(".order__ref").first().textContent())!.trim();
  expect(ref).toMatch(/^W-[0-9A-HJKMNP-TV-Z]{8}$/);
  await expect(page.getByText("Ring size:")).toBeVisible();
  await expect(page.getByText(/Transfer the total to E2E Jewelry/)).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/INV-|QTE-/);
  await capture(page, "3-confirmation-phone");
  await expect(page.getByRole("link", { name: "Bag, 0 items" })).toBeVisible(); // emptied

  // ---- The tracking page, opened fresh from its link ----
  const trackingUrl = page.url();
  const tracking = await phone.newPage();
  await tracking.goto(trackingUrl);
  await expect(tracking.getByRole("heading", { name: `Order ${ref}` })).toBeVisible();
  await expect(tracking.getByText("Waiting for payment")).toBeVisible();
  await expect(tracking.getByText("Shipping to Portland.")).toBeVisible();
  await expect(tracking.locator("body")).not.toContainText("Main St");
  await expect(tracking.locator('meta[name="referrer"]')).toHaveAttribute("content", "no-referrer");
  await capture(tracking, "4-tracking-phone");
  await phone.close();

  // ---- The admin, on a desktop ----
  const desk = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const admin = await desk.newPage();
  await admin.goto(`http://localhost:${PORTS.admin}/sign-in`);
  await admin.getByLabel("Email").fill(s.owner_email);
  await admin.getByLabel("Password").fill(s.owner_password);
  await admin.getByRole("button", { name: "Sign in" }).click();
  await expect(admin.getByLabel("1 website order awaiting payment")).toBeVisible();
  await admin.getByRole("link", { name: /^Invoices/ }).click();
  const row = admin.getByRole("row", { name: new RegExp(ref) });
  await expect(row.getByText("Website")).toBeVisible();
  await expect(admin.getByRole("region", { name: "Website orders" }).getByText("1", { exact: true })).toBeVisible();
  await admin.screenshot({ path: shots("5-admin-invoices"), fullPage: true });

  await row.getByRole("link").first().click();
  const shopper = admin.getByRole("region", { name: "Shopper" });
  await expect(shopper.getByText("sarah@example.com")).toBeVisible();
  await expect(shopper.getByText(/1 Main St/)).toBeVisible();
  await expect(admin.getByRole("list", { name: "Personalization for Halo Ring" })).toContainText("Ring size: 7");
  await admin.screenshot({ path: shots("6-admin-website-order"), fullPage: true });

  // Confirming the payment clears the badge.
  await admin.getByRole("button", { name: "Confirm payment" }).click();
  await admin.getByRole("dialog").getByRole("button", { name: "Confirm payment" }).click();
  await expect(admin.getByLabel(/awaiting payment/)).toHaveCount(0);
  await desk.close();
});

/** Places an order of the necklace on a phone, the way a shopper does; returns its tracking URL and reference. */
async function placeNecklaceOrder(page: Page, origin: string) {
  await page.goto(origin);
  await page.getByRole("link", { name: /Necklace/ }).first().click();
  await page.getByRole("button", { name: "Add to bag" }).click();
  await page.getByRole("link", { name: /^Bag, 1 item/ }).click();
  await page.getByRole("button", { name: "Check out" }).click();
  const opened = Date.now();
  await page.getByLabel("First name").fill("Lena");
  await page.getByLabel("Last name").fill("Ortiz");
  await page.getByLabel("Email").fill("lena@example.com");
  await page.getByLabel("Street address").fill("9 Elm St");
  await page.getByLabel("City").fill("Bangor");
  await page.getByLabel("State").selectOption("ME");
  await page.getByLabel("ZIP code").fill("04401");
  await turnstileToken(page);
  await page.waitForTimeout(Math.max(0, 2_500 - (Date.now() - opened)));
  await page.getByRole("button", { name: /^Place order/ }).click();
  await expect(page.getByRole("heading", { name: "Thank you, Lena" })).toBeVisible();
  const ref = (await page.locator(".order__ref").first().textContent())!.trim();
  return { url: page.url(), ref };
}

test("a deposit: recorded in the admin, production started, the shopper sees what is due", async ({ browser }) => {
  const s = shop();
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const order = await placeNecklaceOrder(await phone.newPage(), s.origin);

  // Tall enough for the whole invoice page: a full-page capture would paint the fixed
  // sidebar in the middle of the page.
  const desk = await browser.newContext({ viewport: { width: 1440, height: 1700 } });
  const admin = await desk.newPage();
  await admin.goto(`http://localhost:${PORTS.admin}/sign-in`);
  await admin.getByLabel("Email").fill(s.owner_email);
  await admin.getByLabel("Password").fill(s.owner_password);
  await admin.getByRole("button", { name: "Sign in" }).click();
  await admin.getByRole("link", { name: /^Invoices/ }).click();
  await admin.getByRole("row", { name: new RegExp(order.ref) }).getByRole("link").first().click();
  const payments = admin.getByRole("region", { name: "Payments" });
  await expect(payments.getByText("No payments recorded yet.")).toBeVisible();

  // The Record payment form, with a $50 bank-transfer deposit typed in.
  await payments.getByRole("button", { name: "Record payment" }).click();
  const form = admin.getByRole("form", { name: "Record a payment" });
  await form.getByLabel("Amount received").fill("50");
  await form.getByRole("radio", { name: "Bank transfer" }).check();
  await form.getByLabel("Note (optional)").fill("Deposit by transfer");
  await admin.screenshot({ path: shots("7-admin-record-payment") });
  await form.getByRole("button", { name: "Record payment" }).click();
  await expect(admin.getByText(/^\$50\.00 recorded\. \$[\d.,]+ still due\.$/)).toBeVisible();
  await expect(admin.locator(".tag--deposit").first()).toHaveText(/^Deposit paid · \$[\d.,]+ due$/);
  await admin.screenshot({ path: shots("8-admin-invoice-deposit") });

  // Production starts on the deposit.
  await payments.getByRole("button", { name: "Start production" }).click();
  await admin.getByRole("dialog").getByRole("button", { name: "Start production" }).click();
  await expect(admin.locator(".tag--deposit").first()).toHaveText(/^Deposit paid · In production · \$[\d.,]+ due$/);
  await admin.screenshot({ path: shots("9-admin-deposit-in-production") });

  // The dashboard holds it apart from revenue.
  await admin.getByRole("link", { name: /^Dashboard/ }).click();
  const held = admin.getByRole("group", { name: "Customer deposits held" });
  await expect(held.getByText("$50.00")).toBeVisible();
  await expect(held.getByText("On 1 open quote. Not revenue until paid in full")).toBeVisible();
  await admin.screenshot({ path: shots("10-admin-dashboard-deposits") });

  // The shopper's tracking page.
  const tracking = await phone.newPage();
  await tracking.goto(order.url);
  await expect(tracking.getByRole("status").filter({ hasText: "In production" })).toContainText(/Your piece is being made\. Balance due \$[\d.,]+\./);
  await expect(tracking.getByRole("region", { name: "Balance due" })).toContainText("Paid so far $50.00");
  await capture(tracking, "11-tracking-deposit-phone");
  await phone.close();
  await desk.close();
});

/** Stripe's pages, stood in for: the browser never reaches Stripe in this run. */
async function standInForStripe(context: import("@playwright/test").BrowserContext) {
  await context.route(/^https:\/\/(connect|checkout)\.stripe\.com\//, (route) =>
    route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Stripe (stand-in)</title><h1>Stripe (stand-in)</h1>" }),
  );
}

test("card: the owner connects Stripe, a shopper pays by card, the invoice is paid by card", async ({ browser }) => {
  const s = shop();

  // ---- The owner connects Stripe (the fake gateway counts onboarding as finished) ----
  const desk = await browser.newContext({ viewport: { width: 1440, height: 1700 } });
  await standInForStripe(desk);
  const admin = await desk.newPage();
  await admin.goto(`http://localhost:${PORTS.admin}/sign-in`);
  await admin.getByLabel("Email").fill(s.owner_email);
  await admin.getByLabel("Password").fill(s.owner_password);
  await admin.getByRole("button", { name: "Sign in" }).click();
  await admin.getByRole("link", { name: /^Settings/ }).click();
  const cards = admin.getByRole("region", { name: "Card payments" });
  await expect(cards.getByText("Not connected")).toBeVisible();
  await cards.scrollIntoViewIfNeeded();
  await admin.screenshot({ path: shots("12-admin-connect-stripe") });
  await cards.getByRole("button", { name: "Connect Stripe" }).click();
  await expect(admin).toHaveURL(/^https:\/\/connect\.stripe\.com\//);
  await admin.goto(`http://localhost:${PORTS.admin}/settings?stripe=return`); // Stripe sends the owner back
  await expect(admin.getByRole("region", { name: "Card payments" }).getByText("Card payments on")).toBeVisible();
  await admin.getByRole("region", { name: "Card payments" }).scrollIntoViewIfNeeded();
  await admin.screenshot({ path: shots("13-admin-stripe-connected") });

  // ---- A shopper picks card at checkout ----
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  await standInForStripe(phone);
  const page = await phone.newPage();
  await page.goto(s.origin);
  await page.getByRole("link", { name: /Necklace/ }).first().click();
  await page.getByRole("button", { name: "Add to bag" }).click();
  await page.getByRole("link", { name: /^Bag, 1 item/ }).click();
  await page.getByRole("button", { name: "Check out" }).click();
  const opened = Date.now();
  await page.getByLabel("First name").fill("Maya");
  await page.getByLabel("Last name").fill("Reyes");
  await page.getByLabel("Email").fill("maya@example.com");
  await page.getByLabel("Street address").fill("4 Pine St");
  await page.getByLabel("City").fill("Augusta");
  await page.getByLabel("State").selectOption("ME");
  await page.getByLabel("ZIP code").fill("04330");
  await page.getByRole("radio", { name: /^Card/ }).check();
  await turnstileToken(page);
  await capture(page, "14-checkout-card-phone");
  await page.waitForTimeout(Math.max(0, 2_500 - (Date.now() - opened)));
  // Read the placed order as it passes (the page leaves for Stripe at once).
  let order: { public_token: string; order_ref: string; checkout_url: string } | undefined;
  await page.route("**/api/v1/public/orders", async (route) => {
    if (route.request().method() !== "POST") return route.continue(); // the CORS preflight
    const response = await route.fetch();
    order = (await response.json()) as typeof order;
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: /^Continue to payment · \$/ }).click();
  await expect.poll(() => order).toBeTruthy();
  if (!order) throw new Error("no order");
  expect(order.checkout_url).toMatch(/^https:\/\/checkout\.stripe\.com\//);
  await expect(page).toHaveURL(/^https:\/\/checkout\.stripe\.com\//); // on to Stripe's page

  // ---- The shopper comes back without paying: the order offers Pay now ----
  const tracking = `${s.origin}/orders/${order.public_token}`;
  await page.goto(tracking);
  const payByCard = page.getByRole("region", { name: "Pay by card" });
  await expect(payByCard.getByRole("button", { name: /^Pay \$[\d.,]+ by card$/ })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Transfer the total");
  await capture(page, "15-tracking-pay-now-phone");

  // ---- Stripe reports the payment (what `stripe trigger` / a real payment would send) ----
  execFileSync(PYTHON, ["scripts/e2e_shop.py", "card-paid", s.tenant_id, order.order_ref, "--api", `http://127.0.0.1:${PORTS.api}`], {
    cwd: BACKEND,
    env: { ...process.env, E2E_STRIPE_WEBHOOK_SECRET },
  });
  await page.goto(`${tracking}?paid=1`);
  await expect(page.getByRole("status").filter({ hasText: "Paid" })).toBeVisible();
  await phone.close();

  // ---- The owner sees an invoice paid by card ----
  await admin.goto(`http://localhost:${PORTS.admin}/invoices`);
  await admin.getByRole("row", { name: new RegExp(order.order_ref) }).getByRole("link").first().click();
  await expect(admin.getByText("Payment · Card (online)")).toBeVisible();
  await expect(admin.getByText(/refunded in your Stripe Dashboard/)).toBeVisible();
  await expect(admin.getByRole("button", { name: "Refund" })).toHaveCount(0);
  await admin.screenshot({ path: shots("16-admin-invoice-paid-by-card") });
  await desk.close();
});

const MAILPIT = "http://127.0.0.1:55324";

test("email: the owner gets a new-order alert; Settings › Emails shows it sent", async ({ browser }) => {
  const s = shop();
  // The worker, once, into the local mail catcher only (never Resend, whatever backend/.env says).
  execFileSync(PYTHON, ["-m", "app.worker", "--once"], {
    cwd: BACKEND,
    env: { ...process.env, EMAIL_PROVIDER: "mailpit", RESEND_API_KEY: "", ADMIN_APP_URL: `http://localhost:${PORTS.admin}` },
  });
  const found = (await (await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:${s.owner_email}`)}`)).json()) as {
    messages: { ID: string; Subject: string }[];
  };
  const alert = found.messages.find((m) => m.Subject.startsWith("New website order W-"));
  expect(alert, "a new-order alert in the mail catcher").toBeTruthy();
  const message = (await (await fetch(`${MAILPIT}/api/v1/message/${alert!.ID}`)).json()) as { HTML: string; Text: string };
  expect(message.Text).not.toMatch(/Main St|Elm St|Pine St|555/); // no street address or phone

  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const mail = await phone.newPage();
  await mail.setContent(message.HTML);
  await expect(mail.getByRole("heading", { name: /^New website order W-/ })).toBeVisible();
  await mail.screenshot({ path: shots("17-email-new-order-alert-phone"), fullPage: true });
  await phone.close();

  const desk = await browser.newContext({ viewport: { width: 1440, height: 1700 } });
  const admin = await desk.newPage();
  await admin.goto(`http://localhost:${PORTS.admin}/sign-in`);
  await admin.getByLabel("Email").fill(s.owner_email);
  await admin.getByLabel("Password").fill(s.owner_password);
  await admin.getByRole("button", { name: "Sign in" }).click();
  await admin.getByRole("link", { name: /^Settings/ }).click();
  const emails = admin.getByRole("region", { name: "Emails" });
  await expect(emails.getByText("Local mail catcher (development)")).toBeVisible();
  await expect(emails.getByRole("table", { name: "Latest emails" }).getByText("Sent").first()).toBeVisible();
  await emails.scrollIntoViewIfNeeded();
  await admin.screenshot({ path: shots("18-admin-settings-emails") });
  await desk.close();
});

