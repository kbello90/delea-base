import { unwrap, type Schemas } from "@delea/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../lib/api";
import { uploadFile } from "../products/uploadFile";
import { brandQueryKey } from "../theme/useBrand";

export type CompanySettings = Schemas["CompanySettingsOut"];
export type CompanyUpdate = Schemas["CompanySettingsUpdate"];
export type BrandConfig = Schemas["BrandConfigOut"];
export type BrandUpdate = Schemas["BrandConfigUpdate"];
export type LogoType = Schemas["LogoType"];
export type Language = Schemas["Language"];

export const LOGO_MAX_BYTES = 2 * 1024 * 1024;
export const LOGO_TYPES: LogoType[] = ["image/jpeg", "image/png", "image/webp"];

export const LANGUAGES: { value: Language; label: string }[] = [
  { value: "en", label: "English" },
  { value: "es", label: "Spanish" },
  { value: "fr", label: "French" },
];

const companyKey = ["settings", "company"] as const;

export function useCompanySettings() {
  const api = useApi();
  return useQuery({ queryKey: companyKey, queryFn: () => unwrap(api.GET("/api/v1/settings/company")) });
}

export function useUpdateCompany() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CompanyUpdate) => unwrap(api.PATCH("/api/v1/settings/company", { body })),
    onSuccess: (company) => client.setQueryData(companyKey, company),
  });
}

/** The same query the header reads, so a saved brand restyles the admin straight away. */
export function useBrandSettings() {
  const api = useApi();
  return useQuery({ queryKey: brandQueryKey, queryFn: () => unwrap(api.GET("/api/v1/settings/brand")) });
}

export function useUpdateBrand() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BrandUpdate) => unwrap(api.PATCH("/api/v1/settings/brand", { body })),
    onSuccess: (brand) => client.setQueryData(brandQueryKey, brand),
  });
}

/** Sign, PUT the file to the signed URL (with progress), then register it. Owner only. */
export function useUploadLogo() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, type, onProgress }: { file: File; type: LogoType; onProgress: (f: number) => void }) => {
      const ticket = await unwrap(api.POST("/api/v1/settings/brand/logo/uploads", { body: { content_type: type } }));
      await uploadFile(ticket.upload_url, file, type, onProgress, LOGO_MAX_BYTES / (1024 * 1024));
      return unwrap(api.POST("/api/v1/settings/brand/logo", { body: { upload_id: ticket.upload_id, content_type: type } }));
    },
    onSuccess: (brand) => client.setQueryData(brandQueryKey, brand),
  });
}

export function useRemoveLogo() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/settings/brand/logo")),
    onSuccess: (brand) => client.setQueryData(brandQueryKey, brand),
  });
}
