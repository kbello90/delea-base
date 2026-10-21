import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "../auth/authContext";
import { createQueryClient } from "./queryClient";

/** TanStack Query, wired to auth: drops all cached data on sign-out, reports no-membership 403s. */
export function QueryProvider({ children }: { children: ReactNode }) {
  const { state, reportNoMembership } = useAuth();
  const [client] = useState(() => createQueryClient(reportNoMembership));
  const signedOut = state.status === "signedOut";

  useEffect(() => {
    if (signedOut) client.clear();
  }, [client, signedOut]);

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
