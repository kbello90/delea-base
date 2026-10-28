import type { AppRole } from "@delea/api-client";
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeBackend, renderApp, tokenFor } from "../test/harness";

// The expected matrix is written out here on purpose (not derived from routeTable.ts), so a
// change to the route table that opens or hides a page has to change this test too.
// It mirrors the backend's require_role matrix.
const PAGES = [
  { path: "/dashboard", label: "Dashboard" },
  { path: "/products", label: "Products" },
  { path: "/materials", label: "Materials" },
  { path: "/customers", label: "Customers" },
  { path: "/invoices", label: "Invoices" },
  { path: "/expenses", label: "Expenses" },
  { path: "/website", label: "Website" },
  { path: "/automations", label: "Automations" },
  { path: "/settings", label: "Settings" },
] as const;

const ALLOWED: Record<AppRole, readonly string[]> = {
  owner: ["Dashboard", "Products", "Materials", "Customers", "Invoices", "Expenses", "Website", "Automations", "Settings"],
  staff: ["Products", "Materials", "Customers", "Invoices"],
  cpa: ["Invoices", "Expenses"],
};

const LANDING: Record<AppRole, string> = { owner: "Dashboard", staff: "Products", cpa: "Invoices" };

const ROLES: AppRole[] = ["owner", "staff", "cpa"];

// Pages that are built (no placeholder). Placeholders say which slice builds them.
const BUILT = new Set(["/dashboard", "/products", "/materials", "/customers", "/invoices", "/expenses", "/website", "/automations", "/settings"]);

const cases = ROLES.flatMap((role) =>
  PAGES.map((page) => ({ role, ...page, allowed: ALLOWED[role].includes(page.label) })),
);

describe("route guard", () => {
  it.each(cases)("$role on $path: allowed=$allowed", async ({ role, path, label, allowed }) => {
    renderApp({ path, backend: fakeBackend(tokenFor(role)) });
    const heading = await screen.findByRole("heading", { level: 1 });
    if (allowed) {
      expect(heading).toHaveTextContent(label);
      if (!BUILT.has(path)) expect(screen.getByText(/^Coming in slice \d$/)).toBeInTheDocument();
    } else {
      expect(heading).toHaveTextContent("You don't have access to this page");
      expect(screen.queryByText(/^Coming in slice/)).not.toBeInTheDocument();
    }
  });

  it.each(ROLES)("%s lands on its start page from /", async (role) => {
    const { location } = renderApp({ path: "/", backend: fakeBackend(tokenFor(role)) });
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(LANDING[role]);
    expect(location.pathname).toBe(`/${LANDING[role].toLowerCase()}`);
  });

  it("an unknown path shows not found inside the shell", async () => {
    renderApp({ path: "/nope" });
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Page not found");
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  it("signed-out users are sent to sign in", async () => {
    const { location } = renderApp({ path: "/invoices", backend: fakeBackend(null) });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(location.pathname).toBe("/sign-in");
  });
});

describe("navigation", () => {
  it.each(ROLES)("%s sees exactly its allowed items", async (role) => {
    renderApp({ path: "/invoices", backend: fakeBackend(tokenFor(role)) });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const items = within(nav)
      .getAllByRole("link")
      .map((link) => link.textContent?.replace(/^\W+/, "").trim());
    expect(items).toEqual(ALLOWED[role]);
  });

  it("marks the current page", async () => {
    renderApp({ path: "/invoices", backend: fakeBackend(tokenFor("cpa")) });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: /Invoices/ })).toHaveAttribute("aria-current", "page");
  });
});
