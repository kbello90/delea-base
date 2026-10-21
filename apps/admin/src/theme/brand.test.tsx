import type { Schemas } from "@delea/api-client";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DEFAULT_BRAND_RESPONSE, fakeApi, fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";
import { toBrand } from "./brand";

const root = () => document.documentElement.style;

function brandApi(overrides: Partial<Schemas["BrandConfigOut"]>) {
  return fakeApi({ "/api/v1/settings/brand": () => jsonResponse(200, { ...DEFAULT_BRAND_RESPONSE, ...overrides }) });
}

describe("toBrand validation", () => {
  it("accepts #RRGGBB colors and an https logo", () => {
    expect(
      toBrand({ business_name: "Atelier", primary_color: "#aaBB11", accent_color: "#000000", logo_url: "https://cdn.example.com/l.png" }),
    ).toEqual({ businessName: "Atelier", logoUrl: "https://cdn.example.com/l.png", colors: { primary: "#aaBB11", accent: "#000000" } });
  });

  it.each([
    ["named color", "red", "#000000"],
    ["short hex", "#abc", "#000000"],
    ["8-digit hex", "#11223344", "#000000"],
    ["css injection", "#112233; background: url(x)", "#000000"],
    ["bad accent", "#112233", "rgb(0,0,0)"],
  ])("falls back to default colors for %s", (_name, primary, accent) => {
    expect(toBrand({ business_name: "A", primary_color: primary, accent_color: accent }).colors).toBeNull();
  });

  it.each(["http://cdn.example.com/l.png", "javascript:alert(1)", "data:image/png;base64,AAAA", "/logo.png", "not a url"])(
    "drops a non-https logo: %s",
    (logo) => {
      expect(toBrand({ business_name: "A", primary_color: "#112233", accent_color: "#445566", logo_url: logo }).logoUrl).toBeNull();
    },
  );

  it("falls back to the DELEA name when the name is blank", () => {
    expect(toBrand({ business_name: "  " }).businessName).toBe("DELEA");
  });
});

describe("brand theming", () => {
  it("applies the owner's brand colors, name and logo", async () => {
    renderApp({ path: "/dashboard", fetch: brandApi({ logo_url: "https://cdn.example.com/logo.png" }) });
    await waitFor(() => expect(root().getPropertyValue("--brand")).toBe("#112233"));
    expect(root().getPropertyValue("--brand-gold")).toBe("#445566");
    expect(root().getPropertyValue("--brand-deep")).toContain("#112233");
    expect(screen.getByTestId("brand-name")).toHaveTextContent("Atelier Rose");
    expect(document.querySelector(".brand-mark img")).toHaveAttribute("src", "https://cdn.example.com/logo.png");
  });

  it("staff gets the brand too", async () => {
    renderApp({ path: "/products", backend: fakeBackend(tokenFor("staff")) });
    await waitFor(() => expect(root().getPropertyValue("--brand")).toBe("#112233"));
  });

  it("invalid values fall back to the DELEA defaults", async () => {
    const fetch = brandApi({ primary_color: "red", logo_url: "http://insecure.example.com/l.png" });
    renderApp({ path: "/dashboard", fetch });
    await waitFor(() => expect(screen.getByTestId("brand-name")).toHaveTextContent("Atelier Rose"));
    expect(root().getPropertyValue("--brand")).toBe("");
    expect(root().getPropertyValue("--brand-gold")).toBe("");
    expect(document.querySelector(".brand-mark img")).toBeNull();
  });

  it("cpa never requests brand config and sees the neutral defaults without an error", async () => {
    const fetch = fakeApi({ "/api/v1/settings/brand": () => jsonResponse(403, { detail: "Insufficient role" }) });
    renderApp({ path: "/invoices", backend: fakeBackend(tokenFor("cpa")), fetch });
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Invoices");
    expect(screen.getByTestId("brand-name")).toHaveTextContent("DELEA");
    expect(fetch).not.toHaveBeenCalled();
    expect(root().getPropertyValue("--brand")).toBe("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("a failed brand load falls back silently", async () => {
    const fetch = fakeApi({ "/api/v1/settings/brand": () => jsonResponse(403, { detail: "Insufficient role" }) });
    renderApp({ path: "/dashboard", fetch });
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.getByTestId("brand-name")).toHaveTextContent("DELEA");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Dashboard");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("removes the brand colors on sign-out", async () => {
    const { backend } = renderApp({ path: "/dashboard" });
    await waitFor(() => expect(root().getPropertyValue("--brand")).toBe("#112233"));
    await backend.signOut();
    await screen.findByRole("heading", { name: "Sign in" });
    expect(root().getPropertyValue("--brand")).toBe("");
  });
});
