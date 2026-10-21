import type { AppRole } from "@delea/api-client";

export interface UiClaims {
  tenantId: string;
  appRole: AppRole;
}

const ROLES: readonly AppRole[] = ["owner", "staff", "cpa"];

function decodePayload(token: string): Record<string, unknown> | null {
  const part = token.split(".")[1];
  if (!part) return null;
  try {
    const base64 = part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=");
    const json = new TextDecoder().decode(Uint8Array.from(atob(base64), (c) => c.charCodeAt(0)));
    const payload: unknown = JSON.parse(json);
    return payload && typeof payload === "object" ? (payload as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/**
 * Reads tenant_id and app_role from the access token issued by Supabase Auth (added by the
 * custom access token hook from tenant_members), never from anything the user can type.
 *
 * COSMETIC ONLY: this decodes without verifying the signature and is used just to choose which
 * navigation and screens to show. The FastAPI backend verifies the token on every request and
 * Postgres RLS enforces tenant and role, so editing the token in the browser grants nothing.
 *
 * Returns null when the token has no usable membership.
 */
export function readUiClaims(accessToken: string): UiClaims | null {
  const payload = decodePayload(accessToken);
  const tenantId = payload?.tenant_id;
  const appRole = payload?.app_role;
  if (typeof tenantId !== "string" || !tenantId) return null;
  if (!ROLES.includes(appRole as AppRole)) return null;
  return { tenantId, appRole: appRole as AppRole };
}

/** The token's subject (the user id), to tell users apart; null if absent. Cosmetic like the above. */
export function readSubject(accessToken: string): string | null {
  const sub = decodePayload(accessToken)?.sub;
  return typeof sub === "string" && sub ? sub : null;
}
