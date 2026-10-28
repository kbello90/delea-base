import { describe, expect, it } from "vitest";
import { adminOrigin, frameAncestors } from "./lib/frameAncestors";

// Website slice: who may frame the storefront. Only itself and the admin's origin from the
// ADMIN_ORIGIN setting; anything missing or odd fails closed to the storefront itself.
describe("frame-ancestors", () => {
  it("lets the storefront and the admin frame it", () => {
    expect(frameAncestors("https://admin.delea.app")).toBe("frame-ancestors 'self' https://admin.delea.app");
    expect(frameAncestors(" https://Admin.Delea.app:8443 ")).toBe("frame-ancestors 'self' https://admin.delea.app:8443");
    expect(frameAncestors("http://localhost:5173")).toBe("frame-ancestors 'self' http://localhost:5173");
  });

  it("fails closed without a setting", () => {
    expect(frameAncestors(undefined)).toBe("frame-ancestors 'self'");
    expect(frameAncestors("")).toBe("frame-ancestors 'self'");
  });

  it.each([
    "*",
    "https://*.delea.app",
    "https://admin.delea.app https://evil.example",
    "https://admin.delea.app,https://evil.example",
    "https://admin.delea.app; script-src *",
    "https://admin.delea.app/settings",
    "https://user@admin.delea.app",
    "admin.delea.app",
    "javascript:alert(1)",
    "http://admin.delea.app", // plain http only for a local host
    "'self'",
  ])("refuses %s: only the storefront itself may frame it", (value) => {
    expect(adminOrigin(value)).toBeNull();
    expect(frameAncestors(value)).toBe("frame-ancestors 'self'");
  });

  it("never lets another site in", () => {
    const header = frameAncestors("https://admin.delea.app");
    expect(header.split(" ")).toEqual(["frame-ancestors", "'self'", "https://admin.delea.app"]);
    expect(header).not.toContain("*");
  });
});
