import { createApiClient, type AppRole, type Schemas } from "@delea/api-client";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AppProviders } from "../App";
import type { AuthBackend, SessionInfo } from "../auth/authBackend";
import { TestRoutes } from "./TestRoutes";

export const API = "http://api.test";

function base64url(value: object): string {
  return btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** An unsigned token with the given claims (the UI only reads claims; it never verifies). */
export function makeToken(claims: Record<string, unknown>): string {
  return `${base64url({ alg: "none" })}.${base64url({ sub: "user-1", ...claims })}.sig`;
}

export const TENANT_A = "11111111-1111-1111-1111-111111111111";

export function tokenFor(role: AppRole): string {
  return makeToken({ tenant_id: TENANT_A, app_role: role });
}

/** A fake AuthBackend holding one session. signIn succeeds unless `control.signInResult` is set. */
export function fakeBackend(token: string | null, email = "person@example.com") {
  let session: SessionInfo | null = token ? { accessToken: token, email } : null;
  const listeners = new Set<(s: SessionInfo | null) => void>();
  const emit = () => listeners.forEach((listener) => listener(session));
  const control = { signInResult: null as string | null, signInToken: tokenFor("owner") };

  return {
    control,
    getSession: vi.fn(async () => session),
    onChange: vi.fn((listener: (s: SessionInfo | null) => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    }),
    signIn: vi.fn(async (signInEmail: string, _password: string): Promise<string | null> => {
      if (control.signInResult) return control.signInResult;
      session = { accessToken: control.signInToken, email: signInEmail };
      emit();
      return null;
    }),
    signOut: vi.fn(async () => {
      session = null;
      emit();
    }),
    getAccessToken: vi.fn(async () => session?.accessToken ?? null),
    refresh: vi.fn(async () => session?.accessToken ?? null),
  } satisfies AuthBackend & { control: object };
}

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export const DEFAULT_BRAND_RESPONSE: Schemas["BrandConfigOut"] = {
  tenant_id: TENANT_A,
  business_name: "Atelier Rose",
  tagline: null,
  primary_color: "#112233",
  accent_color: "#445566",
  logo_url: null,
  hero_title: null,
  hero_subtitle: null,
  updated_at: "2026-10-20T00:00:00Z",
};

type Responder = (request: Request) => Response | Promise<Response>;

/** Fake backend API: brand config by default; override per path with `routes`. */
export function fakeApi(routes: Record<string, Responder> = {}) {
  return vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    const respond = routes[path];
    if (respond) return respond(request);
    if (path === "/api/v1/settings/brand") return jsonResponse(200, DEFAULT_BRAND_RESPONSE);
    return jsonResponse(404, { detail: "Not found" });
  });
}

// MemoryRouter + useRoutes renders the same route objects as the app's data router, without
// the data router's fetch Request (whose jsdom AbortSignal Node's Request rejects).
export function renderApp({
  path = "/",
  backend = fakeBackend(tokenFor("owner")),
  fetch = fakeApi(),
}: {
  path?: string;
  backend?: AuthBackend;
  fetch?: (request: Request) => Promise<Response>;
} = {}) {
  const api = createApiClient({ baseUrl: API, auth: backend, fetch });
  const location = { pathname: path };
  const utils = render(
    <AppProviders backend={backend} api={api}>
      <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TestRoutes location={location} />
      </MemoryRouter>
    </AppProviders>,
  );
  return { ...utils, location, backend, fetch };
}
