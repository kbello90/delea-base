import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useMemo, type ReactNode } from "react";
import { useAuth } from "../auth/authContext";
import { createQueryClient } from "./queryClient";

/**
 * TanStack Query, wired to auth. Each signed-in user gets a fresh QueryClient, and so does the
 * signed-out state: cached data (currency, brand, figures) from one user or tenant can never
 * be read by the next one. The previous client is cleared and dropped.
 *
 * A mounted useQuery keeps the observer it created with the old client, so swapping the client
 * alone is not enough: RequireAuth also remounts the signed-in app per user (keyed by userId).
 * Also reports no-membership 403s.
 */
export function QueryProvider({ children }: { children: ReactNode }) {
  const { state, reportNoMembership } = useAuth();
  const cacheOwner = state.status === "signedIn" ? `user:${state.userId ?? state.email ?? "unknown"}` : "anonymous";
  // reportNoMembership only sets a flag, so the client depends on the cache owner alone.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const client = useMemo(() => createQueryClient(reportNoMembership), [cacheOwner]);

  useEffect(() => () => client.clear(), [client]);

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
