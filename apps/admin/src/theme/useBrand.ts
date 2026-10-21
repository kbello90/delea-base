import { unwrap, type AppRole } from "@delea/api-client";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useApi } from "../lib/api";
import { applyBrandColors, NEUTRAL_BRAND, toBrand, type Brand } from "./brand";

/** Roles that may read brand config (backend: GET /settings/brand is owner or staff). */
const BRAND_READERS: readonly AppRole[] = ["owner", "staff"];

export const brandQueryKey = ["settings", "brand"] as const;

/**
 * The tenant's brand, applied to the page as CSS variables. A role that can't read brand
 * config (cpa), or any failure loading it, gets the neutral DELEA defaults with no error shown:
 * branding is decoration, never a reason to block the app.
 */
export function useBrand(role: AppRole): Brand {
  const api = useApi();
  const canRead = BRAND_READERS.includes(role);
  const { data } = useQuery({
    queryKey: brandQueryKey,
    queryFn: () => unwrap(api.GET("/api/v1/settings/brand")),
    enabled: canRead,
    staleTime: 5 * 60_000,
  });

  const brand = canRead && data ? toBrand(data) : NEUTRAL_BRAND;
  const primary = brand.colors?.primary;
  const accent = brand.colors?.accent;

  useEffect(() => {
    applyBrandColors(primary && accent ? { primary, accent } : null);
    return () => applyBrandColors(null);
  }, [primary, accent]);

  useEffect(() => {
    document.title = `${brand.businessName} Admin`;
  }, [brand.businessName]);

  return brand;
}
