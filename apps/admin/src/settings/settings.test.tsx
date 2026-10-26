import type { AppRole } from "@delea/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { COMPANY_RESPONSE, DEFAULT_BRAND_RESPONSE, fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";
import { FakeXhr, LOGO_BASE, settingsServer } from "./testServer";


function setup(role: AppRole = "owner", server = settingsServer()) {
  const utils = renderApp({ path: "/settings", fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { ...utils, server, user: userEvent.setup() };
}

beforeEach(() => {
  FakeXhr.instances = [];
  FakeXhr.status = 200;
  FakeXhr.delayMs = 10;
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  // The local stack: logos are served over http from its public brand-assets URL.
  vi.stubEnv("VITE_SUPABASE_URL", "http://127.0.0.1:55321");
  vi.stubEnv("VITE_BRAND_ASSETS_BASE_URL", "");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

const business = () => within(screen.getByRole("form", { name: "Business details" }));

describe("business details", () => {
  it("shows the saved values, the currency read-only and the notes", async () => {
    setup();
    expect(await screen.findByLabelText("Legal name")).toHaveValue("DELEA Jewelry LLC");
    expect(screen.getByLabelText("Tax ID (EIN, RUC, VAT…)")).toHaveValue("12-3456789");
    expect(screen.getByLabelText("Default tax rate (%)")).toHaveValue("8");
    expect(screen.getByText("Applies to new quotes. Existing quotes and invoices keep the rate they were created with.")).toBeInTheDocument();
    expect(screen.getByText("USD")).toBeInTheDocument();
    expect(screen.getByText("Set when your account was created; it can't change once you have products or sales.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /currency/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /currency/i })).not.toBeInTheDocument();
    expect(screen.getByText("Used by your online store once it launches.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Manage team" })).toHaveAttribute("href", "/settings/team");
  });

  it.each([
    ["1.15", 115],
    ["7.1", 710],
    ["8.25", 825],
    ["0.07", 7],
    ["16.08", 1608],
  ])("a %s%% tax rate is sent as exactly %i basis points, and nothing else", async (typed, bps) => {
    const { server, user } = setup();
    const rate = await screen.findByLabelText("Default tax rate (%)");
    await user.clear(rate);
    await user.type(rate, typed);
    await user.click(screen.getByRole("button", { name: "Save business details" }));
    await waitFor(() => expect(server.last("PATCH", "/api/v1/settings/company")?.body).toEqual({ default_tax_rate_bps: bps }));
    await waitFor(() => expect(screen.getByLabelText("Default tax rate (%)")).toHaveValue(typed));
  });

  it("refuses an invalid rate before sending anything", async () => {
    const { server, user } = setup();
    const rate = await screen.findByLabelText("Default tax rate (%)");
    for (const [typed, error] of [
      ["8.255", "Use at most 2 decimal places."],
      ["101", "The tax rate can't be more than 100%."],
      ["eight", "Enter the tax rate as a percentage, like 8.25."],
    ]) {
      await user.clear(rate);
      await user.type(rate, typed);
      await user.click(screen.getByRole("button", { name: "Save business details" }));
      expect(await screen.findByText(error)).toBeInTheDocument();
    }
    expect(server.last("PATCH", "/api/v1/settings/company")).toBeUndefined();
  });

  it("sends only the changed fields; the currency is never sent", async () => {
    const { server, user } = setup();
    const taxId = await screen.findByLabelText("Tax ID (EIN, RUC, VAT…)");
    await user.clear(taxId);
    await user.type(taxId, "20100070970");
    await user.click(screen.getByLabelText("Spanish"));
    await user.click(screen.getByRole("button", { name: "Save business details" }));
    await waitFor(() =>
      expect(server.last("PATCH", "/api/v1/settings/company")?.body).toEqual({ ein: "20100070970", enabled_languages: ["en", "es"] }),
    );
    expect(JSON.stringify(server.calls)).not.toContain("currency\":");
  });

  it("the default language must stay turned on", async () => {
    const { server, user } = setup();
    await user.click(await screen.findByLabelText("English"));
    await user.click(screen.getByRole("button", { name: "Save business details" }));
    expect(await screen.findByText("Turn on at least one language.")).toBeInTheDocument();
    expect(server.last("PATCH", "/api/v1/settings/company")).toBeUndefined();
  });

  it("shows the server's message verbatim", async () => {
    const { server, user } = setup();
    server.override("PATCH /api/v1/settings/company", () => jsonResponse(422, { detail: "default_language must be one of enabled_languages" }));
    const name = await screen.findByLabelText("Legal name");
    await user.type(name, " Co");
    await user.click(screen.getByRole("button", { name: "Save business details" }));
    expect(await business().findByRole("alert")).toHaveTextContent("default_language must be one of enabled_languages");
  });
});

describe("brand", () => {
  it("saves only the changed fields and restyles the header straight away", async () => {
    const { server, user } = setup();
    const name = await screen.findByLabelText("Store name");
    await user.clear(name);
    await user.type(name, "Atelier Bleu");
    const primary = screen.getByLabelText("Main colour");
    await user.clear(primary);
    await user.type(primary, "#1f3a5f");
    await user.click(screen.getByRole("button", { name: "Save brand" }));
    await waitFor(() =>
      expect(server.last("PATCH", "/api/v1/settings/brand")?.body).toEqual({ business_name: "Atelier Bleu", primary_color: "#1f3a5f" }),
    );
    await waitFor(() => expect(screen.getByTestId("brand-name")).toHaveTextContent("Atelier Bleu"));
  });

  it("refuses a colour that isn't #RRGGBB", async () => {
    const { server, user } = setup();
    const accent = await screen.findByLabelText("Accent colour");
    await user.clear(accent);
    await user.type(accent, "gold");
    await user.click(screen.getByRole("button", { name: "Save brand" }));
    expect(await screen.findByText("Use a colour like #6B2737.")).toBeInTheDocument();
    expect(server.last("PATCH", "/api/v1/settings/brand")).toBeUndefined();
  });

  it("uploads a logo (sign, PUT to the signed URL, register), shows it, and removes it after confirming", async () => {
    const { server, user } = setup();
    await user.upload(await screen.findByLabelText("Upload a logo"), new File([new Uint8Array(500)], "logo.png", { type: "image/png" }));
    const img = await screen.findByRole("img", { name: "Atelier Rose logo" });
    expect(img.getAttribute("src")).toMatch(new RegExp(`^${LOGO_BASE}/`));
    expect(server.last("POST", "/api/v1/settings/brand/logo/uploads")?.body).toEqual({ content_type: "image/png" });
    expect(FakeXhr.instances.map((x) => [x.method, x.headers["Content-Type"], "Authorization" in x.headers])).toEqual([["PUT", "image/png", false]]);
    expect(server.last("POST", "/api/v1/settings/brand/logo")?.body).toEqual({ upload_id: "u0000000-0000-4000-8000-000000000002", content_type: "image/png" });

    await user.click(screen.getByRole("button", { name: "Remove logo" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove logo" }));
    expect(await screen.findByText("No logo yet.")).toBeInTheDocument();
  });

  it("refuses SVG and files over 2 MB before any request", async () => {
    const { server } = setup();
    const user = userEvent.setup({ applyAccept: false });
    const input = await screen.findByLabelText("Upload a logo");
    await user.upload(input, new File(["<svg/>"], "logo.svg", { type: "image/svg+xml" }));
    expect(await screen.findByText("logo.svg can't be used: choose a JPEG, PNG or WebP image (SVG isn't accepted).")).toBeInTheDocument();
    // The input is reset (remounted) after a refusal, so look it up again.
    await user.upload(screen.getByLabelText("Upload a logo"), new File([new Uint8Array(2 * 1024 * 1024 + 1)], "big.png", { type: "image/png" }));
    expect(await screen.findByText("big.png is larger than 2 MB.")).toBeInTheDocument();
    expect(server.calls.filter((c) => c.path.includes("/logo"))).toEqual([]);
  });

  it("never shows a logo from an unexpected http address", async () => {
    setup("owner", settingsServer({ company: COMPANY_RESPONSE, brand: { ...DEFAULT_BRAND_RESPONSE, logo_url: "http://evil.example/logo.webp" } }));
    expect(await screen.findByText("No logo yet.")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /logo/ })).not.toBeInTheDocument();
  });
});

describe("roles", () => {
  it.each(["staff", "cpa"] as const)("%s can't open Settings", async (role) => {
    const { server } = setup(role);
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("You don't have access to this page");
    expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
    expect(server.calls.filter((c) => c.path === "/api/v1/settings/company")).toEqual([]);
  });
});
