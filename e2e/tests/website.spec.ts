import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { PORTS } from "../playwright.config";

// Website slice: the owner's live preview, and who may frame the storefront.

interface Shop {
  origin: string;
  owner_email: string;
  owner_password: string;
}

const shop = (): Shop => JSON.parse(readFileSync(new URL("../.shop.json", import.meta.url), "utf8")) as Shop;
const shots = (name: string) => `screenshots/${name}.png`;
const ADMIN = `http://localhost:${PORTS.admin}`;

async function signIn(page: Page, s: Shop) {
  await page.goto(`${ADMIN}/sign-in`);
  await page.getByLabel("Email").fill(s.owner_email);
  await page.getByLabel("Password").fill(s.owner_password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/sign-in"));
}

test("the storefront may be framed by itself and the admin only", async ({ browser, request }) => {
  const s = shop();
  for (const path of ["/", "/checkout", "/orders/00000000-0000-4000-8000-000000000000"]) {
    const response = await request.get(`${s.origin}${path}`);
    expect(response.headers()["content-security-policy"], path).toBe(`frame-ancestors 'self' ${ADMIN}`);
  }
  // Another site's frame is refused by the browser: the shop never runs inside it.
  const context = await browser.newContext();
  const page = await context.newPage();
  const messages: unknown[] = [];
  await page.exposeFunction("seen", (data: unknown) => messages.push(data));
  await page.setContent(`<script>addEventListener("message", (e) => seen(e.data))</script><iframe src="${s.origin}/"></iframe>`);
  await page.waitForTimeout(4_000);
  expect(messages).toEqual([]);
  expect(await page.frames()[1]?.locator(".site-header").count()).toBe(0);
  await context.close();
});

test("website: the owner sees the live shop beside its settings, on desktop and phone", async ({ browser }) => {
  const s = shop();
  const desk = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const admin = await desk.newPage();
  await signIn(admin, s);
  await admin.getByRole("link", { name: /^Website/ }).click();
  await expect(admin.getByRole("heading", { level: 1, name: "Website" })).toBeVisible();
  await expect(admin.getByRole("link", { name: "Open my website" })).toHaveAttribute("href", s.origin);
  const preview = admin.frameLocator('iframe[title="Live preview of your website"]');
  await expect(preview.getByRole("link", { name: /E2E Jewelry, home/ })).toBeVisible();
  await admin.waitForTimeout(500);
  await expect(admin.getByRole("alert")).toHaveCount(0); // the preview loaded
  await admin.screenshot({ path: shots("23-admin-website") });

  // Phone width: the preview on top, the settings below.
  const small = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const phone = await small.newPage();
  await signIn(phone, s);
  await phone.goto(`${ADMIN}/website`);
  await phone.getByRole("button", { name: "Phone" }).click();
  await expect(phone.frameLocator('iframe[title="Live preview of your website"]').getByRole("link", { name: /E2E Jewelry, home/ })).toBeVisible();
  await phone.addStyleTag({ content: ".topbar { position: static !important; }" });
  await phone.screenshot({ path: shots("23-admin-website-phone") }); // one screen: the header and the preview
  await phone.screenshot({ path: shots("23-admin-website-phone-full"), fullPage: true });
  await small.close();
  await desk.close();
});

test("automations: every automatic email, its trigger and whether it is on; read only", async ({ browser }) => {
  const s = shop();
  const desk = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const admin = await desk.newPage();
  await signIn(admin, s);
  await admin.getByRole("link", { name: /^Automations/ }).click();
  const table = admin.getByRole("table", { name: "Automatic emails" });
  await expect(table.getByRole("row")).toHaveCount(9);
  await expect(admin.getByRole("checkbox")).toHaveCount(0);
  await admin.screenshot({ path: shots("24-admin-automations") });
  await admin.getByRole("link", { name: "Change in Settings › Emails" }).click();
  await expect(admin.getByRole("heading", { name: "Emails", exact: true })).toBeInViewport();
  await desk.close();
});
