import { unwrap, type Schemas } from "@delea/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../lib/api";

export type EmailSettings = Schemas["EmailSettingsOut"];
export type EmailSettingsUpdate = Schemas["EmailSettingsUpdate"];
export type OwnerAlerts = Schemas["OwnerAlerts"];
export type CustomerEmails = Schemas["CustomerEmails"];
export type CustomerTemplate = keyof CustomerEmails;
export type EmailPreview = Schemas["EmailPreviewOut"];

const key = ["settings", "emails"] as const;

/** The shop's email settings and its latest emails (owner). */
export function useEmailSettings() {
  const api = useApi();
  return useQuery({ queryKey: key, queryFn: () => unwrap(api.GET("/api/v1/settings/emails")) });
}

export function useUpdateEmailSettings() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: EmailSettingsUpdate) => unwrap(api.PATCH("/api/v1/settings/emails", { body })),
    onSuccess: (data) => client.setQueryData(key, data),
  });
}

/** Emails a one-time confirmation link to the address; it is used only once confirmed. */
export function useRequestReplyTo() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (email: string) => unwrap(api.POST("/api/v1/settings/emails/reply-to", { body: { email } })),
    onSuccess: (data) => client.setQueryData(key, data),
  });
}

/** A customer email rendered with a made-up order (nothing is sent); `intro` may be unsaved. */
export function usePreviewEmail() {
  const api = useApi();
  return useMutation({
    mutationFn: (body: { template: CustomerTemplate; intro: string | null }) =>
      unwrap(api.POST("/api/v1/settings/emails/preview", { body })),
  });
}

/** A test to the signed-in owner's own email: the plain test, or one customer email. */
export function useSendTestEmail() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (template?: CustomerTemplate) =>
      unwrap(api.POST("/api/v1/settings/emails/test", template ? { body: { template } } : {})),
    onSuccess: () => void client.invalidateQueries({ queryKey: key }),
  });
}
