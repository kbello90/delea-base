import { createApiClient, type ApiClient, type AuthHandlers } from "@delea/api-client";
import { createContext, useContext } from "react";

/** The admin app's API client: FastAPI at VITE_API_URL, authenticated with the Supabase session. */
export function createAdminApi(auth: AuthHandlers): ApiClient {
  return createApiClient({ baseUrl: import.meta.env.VITE_API_URL ?? "http://localhost:8000", auth });
}

export const ApiContext = createContext<ApiClient | null>(null);

export function useApi(): ApiClient {
  const api = useContext(ApiContext);
  if (!api) throw new Error("useApi must be used inside <ApiContext.Provider>");
  return api;
}
