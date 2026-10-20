import { aiEndpoints } from "./endpoints/ai";
import { automationEndpoints } from "./endpoints/automation";
import { expensesEndpoints } from "./endpoints/expenses";
import { healthEndpoints } from "./endpoints/health";
import { invoicesEndpoints } from "./endpoints/invoices";
import { materialsEndpoints } from "./endpoints/materials";
import { productsEndpoints } from "./endpoints/products";
import { settingsEndpoints } from "./endpoints/settings";
import { createHttpClient } from "./http";

export type { HttpClient } from "./http";
export * from "./types";

/** Typed client for the FastAPI backend. */
export function createApiClient(baseUrl: string) {
  const http = createHttpClient(baseUrl);
  return {
    health: healthEndpoints(http),
    products: productsEndpoints(http),
    materials: materialsEndpoints(http),
    invoices: invoicesEndpoints(http),
    expenses: expensesEndpoints(http),
    automation: automationEndpoints(http),
    settings: settingsEndpoints(http),
    ai: aiEndpoints(http),
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;
