import { createApiClient, type ApiClient } from "@delea/api-client";
import { createContext, useContext } from "react";

/**
 * The storefront's API client: the public FastAPI endpoints only (TRD §1.3), no sign-in. The
 * API finds the shop from the browser's Origin header, so nothing here names a tenant.
 */
export function createStorefrontApi(fetchImpl?: (input: Request) => Promise<Response>): ApiClient {
  return createApiClient({ baseUrl: import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000", fetch: fetchImpl });
}

export const ApiContext = createContext<ApiClient | null>(null);

export function useApi(): ApiClient {
  const api = useContext(ApiContext);
  if (!api) throw new Error("useApi must be used inside <ApiContext.Provider>");
  return api;
}
