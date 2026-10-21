import type { AppRole } from "@delea/api-client";
import type { ReactNode } from "react";
import { useAuth } from "../auth/authContext";
import { ForbiddenPage } from "./pages";

/**
 * Shows the page only to the listed roles; anyone else gets ForbiddenPage. This is UX: the
 * backend's require_role and RLS are the enforcement.
 */
export function RequireRole({ roles, children }: { roles: readonly AppRole[]; children: ReactNode }) {
  const { state } = useAuth();
  const role = state.status === "signedIn" ? state.claims?.appRole : undefined;
  if (!role || !roles.includes(role)) return <ForbiddenPage />;
  return <>{children}</>;
}
