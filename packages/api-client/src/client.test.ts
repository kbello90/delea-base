import { describe, expect, it, vi } from "vitest";
import { ACCESS_HEADER, ApiError, createApiClient, NO_MEMBERSHIP, toApiError, unwrap, type AuthHandlers, type Schemas } from "./index";

const BASE = "http://api.test";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Sent {
  url: string;
  method: string;
  auth: string | null;
  body: string;
}

/** A fake server: `respond` decides each response; every request is recorded with its body. */
function fakeFetch(respond: (sent: Sent, index: number) => Response | Promise<Response>) {
  const sent: Sent[] = [];
  const fetch = vi.fn(async (request: Request) => {
    const entry = {
      url: request.url,
      method: request.method,
      auth: request.headers.get("Authorization"),
      body: await request.text(),
    };
    sent.push(entry);
    return respond(entry, sent.length - 1);
  });
  return { fetch, sent };
}

function fakeAuth(tokens: { current: string | null; refreshed: string | null }) {
  const auth = {
    getAccessToken: vi.fn(async () => tokens.current),
    refresh: vi.fn(async () => {
      tokens.current = tokens.refreshed;
      return tokens.refreshed;
    }),
    signOut: vi.fn(async () => {
      tokens.current = null;
    }),
  } satisfies AuthHandlers;
  return auth;
}

const BRAND = { business_name: "Atelier" };
const NEW_CUSTOMER: Schemas["CustomerCreate"] = {
  customer_type: "retail",
  first_name: "Ana",
  country: "US",
  marketing_opt_in: false,
  tax_exempt: false,
  wholesale_discount_bps: 0,
  payment_terms: "due_on_receipt",
};

describe("auth middleware", () => {
  it("attaches the access token as a Bearer header", async () => {
    const { fetch, sent } = fakeFetch(() => json(200, BRAND));
    const auth = fakeAuth({ current: "tok-1", refreshed: null });
    const api = createApiClient({ baseUrl: BASE, auth, fetch });

    await expect(unwrap(api.GET("/api/v1/settings/brand"))).resolves.toEqual(BRAND);
    expect(sent).toHaveLength(1);
    expect(sent[0].auth).toBe("Bearer tok-1");
    expect(sent[0].url).toBe(`${BASE}/api/v1/settings/brand`);
  });

  it("on 401 refreshes once and retries once with the new token and the same body", async () => {
    const { fetch, sent } = fakeFetch((req) =>
      req.auth === "Bearer fresh" ? json(201, { id: "c1" }) : json(401, { detail: "Invalid token" }),
    );
    const auth = fakeAuth({ current: "stale", refreshed: "fresh" });
    const api = createApiClient({ baseUrl: BASE, auth, fetch });

    await expect(unwrap(api.POST("/api/v1/customers", { body: NEW_CUSTOMER }))).resolves.toEqual({ id: "c1" });
    expect(auth.refresh).toHaveBeenCalledTimes(1);
    expect(auth.signOut).not.toHaveBeenCalled();
    expect(sent.map((s) => s.auth)).toEqual(["Bearer stale", "Bearer fresh"]);
    expect(sent[1].method).toBe("POST");
    expect(JSON.parse(sent[1].body)).toEqual(NEW_CUSTOMER);
  });

  it("a second 401 signs out and does not retry again", async () => {
    // Loop guard: a client that retries forever would never yield (the fake resolves at once),
    // so after 10 calls the fake answers 500 to end the loop and let the assertions fail.
    const { fetch } = fakeFetch((_req, i) => (i < 10 ? json(401, { detail: "Invalid token" }) : json(500, {})));
    const auth = fakeAuth({ current: "stale", refreshed: "also-rejected" });
    const api = createApiClient({ baseUrl: BASE, auth, fetch });

    const error = await unwrap(api.GET("/api/v1/settings/brand")).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(auth.refresh).toHaveBeenCalledTimes(1);
    expect(auth.signOut).toHaveBeenCalledTimes(1);
  });

  it("signs out without retrying when the refresh fails", async () => {
    const { fetch } = fakeFetch(() => json(401, { detail: "Invalid token" }));
    const auth = fakeAuth({ current: "stale", refreshed: null });
    const api = createApiClient({ baseUrl: BASE, auth, fetch });

    await expect(unwrap(api.GET("/api/v1/settings/brand"))).rejects.toMatchObject({ status: 401 });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(auth.signOut).toHaveBeenCalledTimes(1);
  });

  it("concurrent 401s share one refresh and each retries once", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const { fetch, sent } = fakeFetch((req) =>
      req.auth === "Bearer fresh" ? json(200, BRAND) : json(401, { detail: "Invalid token" }),
    );
    const tokens = { current: "stale" as string | null, refreshed: "fresh" as string | null };
    const auth = fakeAuth(tokens);
    auth.refresh.mockImplementation(async () => {
      await gate; // hold the refresh open until every request has hit its 401
      tokens.current = "fresh";
      return "fresh";
    });
    const api = createApiClient({ baseUrl: BASE, auth, fetch });

    const calls = [1, 2, 3].map(() => unwrap(api.GET("/api/v1/settings/brand")));
    await vi.waitFor(() => expect(sent.filter((s) => s.auth === "Bearer stale")).toHaveLength(3));
    release();

    await expect(Promise.all(calls)).resolves.toEqual([BRAND, BRAND, BRAND]);
    expect(auth.refresh).toHaveBeenCalledTimes(1);
    expect(sent.filter((s) => s.auth === "Bearer fresh")).toHaveLength(3);
    expect(fetch).toHaveBeenCalledTimes(6);
    expect(auth.signOut).not.toHaveBeenCalled();
  });

  it("never refreshes or retries a Supabase auth call", async () => {
    const { fetch } = fakeFetch(() => json(401, { detail: "nope" }));
    const auth = fakeAuth({ current: "stale", refreshed: "fresh" });
    // An auth URL routed through this client by mistake (not a typed path, hence the cast).
    const api = createApiClient({ baseUrl: "http://127.0.0.1:55321/auth/v1", auth, fetch });

    await expect(unwrap(api.GET("/token" as "/health"))).rejects.toMatchObject({ status: 401 });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(auth.refresh).not.toHaveBeenCalled();
    expect(auth.signOut).not.toHaveBeenCalled();
  });
});

describe("ApiError normalization", () => {
  it.each([
    [403, { detail: "Insufficient role" }, "Insufficient role"],
    [403, { detail: "No tenant membership" }, "No tenant membership"],
    [409, { detail: "This invoice has payments and can't be deleted" }, "This invoice has payments and can't be deleted"],
    [422, { detail: [{ loc: ["body", "price_cents"], msg: "Input should be greater than 0" }] }, "price_cents: Input should be greater than 0"],
    [404, {}, "Not found."],
    [500, "Internal Server Error", "Something went wrong on the server."],
  ])("status %i", (status, body, message) => {
    const error = toApiError(status, body);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(status);
    expect(error.message).toBe(message);
  });

  it("unwrap throws an ApiError with the server's message", async () => {
    const { fetch } = fakeFetch(() => json(409, { detail: "SKU already exists" }));
    const api = createApiClient({ baseUrl: BASE, fetch });
    await expect(unwrap(api.GET("/api/v1/settings/brand"))).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
      message: "SKU already exists",
    });
  });

  it("a network failure becomes status 0", async () => {
    const fetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    const api = createApiClient({ baseUrl: BASE, fetch });
    await expect(unwrap(api.GET("/api/v1/settings/brand"))).rejects.toMatchObject({ status: 0 });
  });
});

describe("membership signals", () => {
  const stale = () => json(401, { detail: "Membership no longer valid" });
  const noMembership = () =>
    new Response(JSON.stringify({ detail: "No tenant membership" }), {
      status: 403,
      headers: { "Content-Type": "application/json", [ACCESS_HEADER]: NO_MEMBERSHIP },
    });

  it("a changed role: the 401 refreshes once and the retry with the new token succeeds", async () => {
    const { fetch, sent } = fakeFetch((req) => (req.auth === "Bearer new-role" ? json(200, BRAND) : stale()));
    const auth = fakeAuth({ current: "old-role", refreshed: "new-role" });
    const api = createApiClient({ baseUrl: BASE, auth, fetch });

    await expect(unwrap(api.GET("/api/v1/settings/brand"))).resolves.toEqual(BRAND);
    expect(auth.refresh).toHaveBeenCalledTimes(1);
    expect(auth.signOut).not.toHaveBeenCalled();
    expect(sent.map((s) => s.auth)).toEqual(["Bearer old-role", "Bearer new-role"]);
  });

  it("a removed member: after the one refresh the 403 carries the no-membership code", async () => {
    const { fetch } = fakeFetch((req) => (req.auth === "Bearer no-claims" ? noMembership() : stale()));
    const auth = fakeAuth({ current: "removed", refreshed: "no-claims" });
    const api = createApiClient({ baseUrl: BASE, auth, fetch });

    const error = await unwrap(api.GET("/api/v1/settings/brand")).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, code: NO_MEMBERSHIP });
    expect(auth.refresh).toHaveBeenCalledTimes(1);
  });

  it("the code comes from the header only: the same message without it has no code", async () => {
    const { fetch } = fakeFetch(() => json(403, { detail: "No tenant membership" }));
    const api = createApiClient({ baseUrl: BASE, fetch });
    const error = await unwrap(api.GET("/api/v1/settings/brand")).catch((e: unknown) => e);
    expect(error).toMatchObject({ status: 403, code: undefined });
  });
});
