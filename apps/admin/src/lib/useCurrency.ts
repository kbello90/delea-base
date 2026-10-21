import { unwrap } from "@delea/api-client";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/authContext";
import { useApi } from "./api";

export type CurrencyState =
  | { status: "pending" }
  | { status: "error"; error: unknown; retry: () => void }
  | { status: "success"; currency: string };

/**
 * The tenant's currency from GET /settings/currency (owner, staff and cpa). There is no
 * fallback: money is only formatted once the real currency is known, so callers show
 * skeletons while pending and a message with retry on error.
 */
export function useCurrency(): CurrencyState {
  const api = useApi();
  const { state } = useAuth();
  const isMember = state.status === "signedIn" && state.claims !== null;
  const query = useQuery({
    queryKey: ["settings", "currency"],
    queryFn: () => unwrap(api.GET("/api/v1/settings/currency")),
    enabled: isMember,
    staleTime: 5 * 60_000,
  });
  if (query.isSuccess) return { status: "success", currency: query.data.currency };
  if (query.isError) return { status: "error", error: query.error, retry: () => void query.refetch() };
  return { status: "pending" };
}
