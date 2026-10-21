import { unwrap } from "@delea/api-client";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/authContext";
import { useApi } from "./api";
import { FALLBACK_CURRENCY } from "./money";

export const companySettingsQueryKey = ["settings", "company"] as const;

/**
 * The tenant's currency code, for formatMoney. USD until it has loaded, or if it can't be read.
 *
 * Read from GET /settings/company, which only the owner may call; other roles get the fallback
 * for now. A follow-up task adds GET /settings/currency for every role and switches this hook.
 */
export function useCurrency(): string {
  const api = useApi();
  const { state } = useAuth();
  const isOwner = state.status === "signedIn" && state.claims?.appRole === "owner";
  const { data } = useQuery({
    queryKey: companySettingsQueryKey,
    queryFn: () => unwrap(api.GET("/api/v1/settings/company")),
    enabled: isOwner,
    staleTime: 5 * 60_000,
  });
  return data?.currency ?? FALLBACK_CURRENCY;
}
