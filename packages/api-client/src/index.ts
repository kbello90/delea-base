import createClient, { type Client, type Middleware } from "openapi-fetch";
import { ACCESS_HEADER, ApiError, toApiError } from "./errors";
import type { components, paths } from "./schema";

export { ACCESS_HEADER, ApiError, NO_MEMBERSHIP, toApiError } from "./errors";
export type { components, paths } from "./schema";

/** Value of the `app_role` JWT claim (TRD §2.2). */
export type AppRole = "owner" | "staff" | "cpa";

export type Schemas = components["schemas"];

/** How the client gets and renews the caller's token. Kept abstract so this package has no Supabase dependency. */
export interface AuthHandlers {
  /** The current access token, or null when signed out. */
  getAccessToken(): Promise<string | null>;
  /** Refreshes the session and returns the new access token, or null if that failed. */
  refresh(): Promise<string | null>;
  /** Called when the session can't be recovered (refresh failed or the retry was still 401). */
  signOut(): Promise<void>;
}

export interface ApiClientOptions {
  baseUrl: string;
  auth?: AuthHandlers;
  fetch?: (input: Request) => Promise<Response>;
}

export type ApiClient = Client<paths>;

// Sign-in and token refresh are supabase-js calls and never go through this client. This guard
// keeps it that way if one is ever routed here: an auth request is never refreshed or retried.
const AUTH_PATH = /\/auth\/v1\//;

function withToken(request: Request, token: string): Request {
  const next = new Request(request);
  next.headers.set("Authorization", `Bearer ${token}`);
  return next;
}

/**
 * Attaches the bearer token, and on a 401 refreshes the session once and retries once.
 * Concurrent 401s share one in-flight refresh. A second 401, or a failed refresh, signs out.
 *
 * A 401 also means "this token's membership changed" (removed, role changed, business
 * deactivated): the refreshed token carries the membership as it is now, so a role change just
 * works on the retry, and an account with no membership left gets a 403 marked no-membership.
 */
export function authMiddleware(auth: AuthHandlers): Middleware {
  // An untouched copy of each request, taken before it is sent: a sent body can't be reread.
  const originals = new Map<string, Request>();
  let refreshing: Promise<string | null> | null = null;

  const refreshOnce = (): Promise<string | null> => {
    refreshing ??= auth.refresh().finally(() => {
      refreshing = null;
    });
    return refreshing;
  };

  return {
    async onRequest({ request, id }) {
      if (AUTH_PATH.test(request.url)) return undefined;
      const token = await auth.getAccessToken();
      const authed = token ? withToken(request, token) : request;
      originals.set(id, authed.clone());
      return authed;
    },

    async onResponse({ response, id, options }) {
      const original = originals.get(id);
      originals.delete(id);
      if (response.status !== 401 || !original) return undefined;

      const token = await refreshOnce();
      if (!token) {
        await auth.signOut();
        return undefined;
      }
      // Exactly one retry, sent straight to fetch so it never re-enters this middleware.
      const retried = await options.fetch(withToken(original, token));
      if (retried.status === 401) await auth.signOut();
      return retried;
    },

    onError({ id }) {
      originals.delete(id);
      return undefined;
    },
  };
}

/** Typed client for the FastAPI backend, generated from packages/api-client/openapi.json. */
export function createApiClient({ baseUrl, auth, fetch }: ApiClientOptions): ApiClient {
  const client = createClient<paths>({
    baseUrl: baseUrl.replace(/\/+$/, ""),
    // Resolved per call (not captured at creation), so a replaced global fetch is honoured.
    fetch: fetch ?? ((input: Request) => globalThis.fetch(input)),
  });
  if (auth) client.use(authMiddleware(auth));
  return client;
}

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/**
 * Resolves an openapi-fetch call to its data, or throws an ApiError.
 *     const brand = await unwrap(api.GET("/api/v1/settings/brand"));
 */
export async function unwrap<T>(call: Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await call;
  } catch (cause) {
    if (cause instanceof ApiError) throw cause;
    throw toApiError(0, undefined);
  }
  if (!result.response.ok) {
    const code = result.response.headers.get(ACCESS_HEADER) ?? undefined;
    throw toApiError(result.response.status, result.error, code);
  }
  return result.data as T;
}
