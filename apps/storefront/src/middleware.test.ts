import { afterEach, describe, expect, it } from "vitest";
import middleware, { config } from "../middleware";

// The hosted header (Vercel Routing Middleware): the same value as the dev and preview servers.
describe("vercel middleware", () => {
  const saved = process.env.ADMIN_ORIGIN;
  afterEach(() => {
    if (saved === undefined) delete process.env.ADMIN_ORIGIN;
    else process.env.ADMIN_ORIGIN = saved;
  });

  it("adds frame-ancestors with the admin origin from the environment", () => {
    process.env.ADMIN_ORIGIN = "https://admin.delea.app";
    const response = middleware();
    expect(response.headers.get("Content-Security-Policy")).toBe("frame-ancestors 'self' https://admin.delea.app");
    expect(response.headers.get("x-middleware-next")).toBe("1"); // the page itself is served as usual
  });

  it("fails closed without it", () => {
    delete process.env.ADMIN_ORIGIN;
    expect(middleware().headers.get("Content-Security-Policy")).toBe("frame-ancestors 'self'");
  });

  it("runs on pages, not on built files", () => {
    expect(config.matcher).toEqual(["/((?!assets/).*)"]);
  });
});
