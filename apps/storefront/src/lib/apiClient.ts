import { createApiClient } from "@delea/api-client";

/** Thin wrapper around packages/api-client, bound to this app's backend URL. */
export const apiClient = createApiClient(import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000");
