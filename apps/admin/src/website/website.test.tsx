import type { AppRole } from "@delea/api-client";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_BRAND_RESPONSE, fakeApi, fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";
import { PREVIEW_TIMEOUT_MS, READY_MESSAGE } from "./preview";

// Website slice: the owner's live preview beside the Brand and Storefront settings.

const SHOP = "http://atelier-rose.localhost:5174";

function setup({ website = { url: SHOP, enabled: true }, role = "owner" }: { website?: { url: string | null; enabled: boolean }; role?: AppRole } = {}) {
  let brand = { ...DEFAULT_BRAND_RESPONSE };
  const fetch = fakeApi({
    "/api/v1/settings/website": () => jsonResponse(200, website),
    "/api/v1/settings/brand": async (request) => {
      if (request.method === "PATCH") brand = { ...brand, ...((await request.json()) as object) };
      return jsonResponse(200, brand);
    },
  });
  renderApp({ path: "/website", fetch, backend: fakeBackend(tokenFor(role)) });
  return { fetch, user: userEvent.setup() };
}

const frame = () => screen.findByTitle("Live preview of your website");
const ready = (origin = SHOP) => act(() => window.dispatchEvent(new MessageEvent("message", { origin, data: { type: READY_MESSAGE } })));

afterEach(() => vi.useRealTimers());

describe("website page", () => {
  it("shows the owner's own website live, with Open my website", async () => {
    setup();
    expect(await screen.findByRole("heading", { level: 1, name: "Website" })).toBeInTheDocument();
    const iframe = await frame();
    expect(iframe).toHaveAttribute("src", SHOP);
    // Its own scripts, but it can never navigate the admin.
    expect(iframe.getAttribute("sandbox")).toContain("allow-scripts");
    expect(iframe.getAttribute("sandbox")).not.toContain("allow-top-navigation");
    const open = screen.getByRole("link", { name: "Open my website" });
    expect(open).toHaveAttribute("href", SHOP);
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", "noopener noreferrer");
    // The settings that shape it are beside it.
    expect(await screen.findByRole("region", { name: "Brand" })).toBeInTheDocument();
    expect(await screen.findByRole("form", { name: "Storefront" })).toBeInTheDocument();
    expect(screen.getByText(/an order placed in the preview is a real order/)).toBeInTheDocument();
  });

  it("starts at phone width and switches to desktop and back", async () => {
    const { user } = setup();
    expect(await frame()).toHaveAttribute("width", "390");
    expect(screen.getByRole("button", { name: "Phone" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Desktop" }));
    expect(screen.getByRole("button", { name: "Desktop" })).toHaveAttribute("aria-pressed", "true");
    expect(await frame()).toHaveAttribute("width", "1280");
    await user.click(screen.getByRole("button", { name: "Phone" }));
    expect(await frame()).toHaveAttribute("width", "390");
  });

  it("says when the preview can't load, and Open my website still works", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setup();
    await frame();
    ready("https://evil.example"); // only the shop's own page counts
    act(() => void vi.advanceTimersByTime(PREVIEW_TIMEOUT_MS + 10));
    expect(await screen.findByRole("alert")).toHaveTextContent("The preview couldn't load here.");
    expect(screen.getByRole("link", { name: "Open my website" })).toHaveAttribute("href", SHOP);
  });

  it("a preview that loads says nothing more", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setup();
    await frame();
    ready();
    act(() => void vi.advanceTimersByTime(PREVIEW_TIMEOUT_MS + 10));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("no domain yet: a clear message, no frame, nothing to open", async () => {
    setup({ website: { url: null, enabled: false } });
    expect(await screen.findByText(/Your website has no address yet/)).toBeInTheDocument();
    expect(screen.queryByTitle("Live preview of your website")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open my website" })).not.toBeInTheDocument();
  });

  it("a switched-off storefront says so above the preview", async () => {
    setup({ website: { url: SHOP, enabled: false } });
    expect(await screen.findByText(/Your storefront is switched off/)).toBeInTheDocument();
    expect(await frame()).toHaveAttribute("src", SHOP);
  });

  it("reloads the preview after Save", async () => {
    const { user } = setup();
    const before = await frame();
    const brand = within(await screen.findByRole("region", { name: "Brand" }));
    const name = brand.getByLabelText("Store name");
    await user.clear(name);
    await user.type(name, "Atelier Rouge");
    await user.click(brand.getByRole("button", { name: "Save brand" }));
    expect(await screen.findByText("Brand saved")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTitle("Live preview of your website")).not.toBe(before));
  });

  it.each(["staff", "cpa"] as const)("is not shown to %s, and its address is never asked for", async (role) => {
    const { fetch } = setup({ role });
    expect(await screen.findByRole("navigation", { name: "Main" })).not.toHaveTextContent("Website");
    expect(screen.queryByTitle("Live preview of your website")).not.toBeInTheDocument();
    const asked = fetch.mock.calls.some(([request]) => new URL((request as Request).url).pathname === "/api/v1/settings/website");
    expect(asked).toBe(false);
  });
});
