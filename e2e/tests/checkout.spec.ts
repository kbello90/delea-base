import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { PORTS } from "../playwright.config";

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
