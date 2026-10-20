// Mirrors backend Pydantic schemas (backend/app/models/).
// Entity types (products, invoices, …) are added once the schema is approved.

export interface HealthResponse {
  status: "ok";
}

/** Value of the `app_role` JWT claim (TRD §2.2). */
export type AppRole = "owner" | "staff" | "cpa";

/** `order_status` enum on invoices (TRD §3.1). */
export type OrderStatus = "quote" | "paid" | "in_production" | "shipped" | "delivered";
