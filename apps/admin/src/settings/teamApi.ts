import { unwrap, type Schemas } from "@delea/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../lib/api";

export type Member = Schemas["MemberOut"];
export type InvitableRole = Schemas["MemberInvite"]["app_role"];

export const INVITABLE_ROLES: { value: InvitableRole; label: string; hint: string }[] = [
  { value: "staff", label: "Staff", hint: "Products, materials, customers and sales" },
  { value: "cpa", label: "Accountant (CPA)", hint: "Invoices and expenses, read only" },
];

const teamKey = ["team"] as const;

export function useTeam() {
  const api = useApi();
  return useQuery({ queryKey: teamKey, queryFn: () => unwrap(api.GET("/api/v1/members")) });
}

function useTeamMutation<V, R>(call: (api: ReturnType<typeof useApi>, vars: V) => Promise<R>) {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: V) => call(api, vars),
    onSettled: () => client.invalidateQueries({ queryKey: teamKey }),
  });
}

export function useInvite() {
  return useTeamMutation((api, body: Schemas["MemberInvite"]) => unwrap(api.POST("/api/v1/members", { body })));
}

export function useResendInvite() {
  return useTeamMutation((api, userId: string) =>
    unwrap(api.POST("/api/v1/members/{user_id}/resend-invite", { params: { path: { user_id: userId } } })),
  );
}

export function useChangeRole() {
  return useTeamMutation((api, { userId, role }: { userId: string; role: InvitableRole }) =>
    unwrap(api.PATCH("/api/v1/members/{user_id}", { params: { path: { user_id: userId } }, body: { app_role: role } })),
  );
}

export function useRemoveMember() {
  return useTeamMutation((api, userId: string) =>
    unwrap(api.DELETE("/api/v1/members/{user_id}", { params: { path: { user_id: userId } } })),
  );
}
