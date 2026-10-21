import type { AppRole } from "@delea/api-client";

export interface AppRoute {
  path: string;
  label: string;
  /** Who sees this page. Mirrors the backend's require_role matrix; the backend and RLS enforce. */
  roles: readonly AppRole[];
  /** Decorative sidebar glyph (from docs/delea-demo.html). */
  glyph: string;
  /** The slice that builds the real page (placeholder text until then). */
  slice: number;
}

export const ROUTES: readonly AppRoute[] = [
  { path: "/dashboard", label: "Dashboard", roles: ["owner"], glyph: "◆", slice: 7 },
  { path: "/products", label: "Products", roles: ["owner", "staff"], glyph: "○", slice: 2 },
  { path: "/materials", label: "Materials", roles: ["owner", "staff"], glyph: "▤", slice: 3 },
  { path: "/customers", label: "Customers", roles: ["owner", "staff"], glyph: "◇", slice: 4 },
  { path: "/invoices", label: "Invoices", roles: ["owner", "staff", "cpa"], glyph: "▢", slice: 5 },
  { path: "/expenses", label: "Expenses", roles: ["owner", "cpa"], glyph: "▥", slice: 6 },
  { path: "/settings", label: "Settings", roles: ["owner"], glyph: "⚙", slice: 8 },
];

export const DEFAULT_LANDING: Record<AppRole, string> = {
  owner: "/dashboard",
  staff: "/products",
  cpa: "/invoices",
};

export const ROLE_LABEL: Record<AppRole, string> = {
  owner: "Owner",
  staff: "Staff",
  cpa: "CPA",
};

export function routesFor(role: AppRole): AppRoute[] {
  return ROUTES.filter((route) => route.roles.includes(role));
}
