import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/** The slice of supabase-js the admin app may use: sign-in, session and token refresh. */
export type SupabaseAuth = SupabaseClient["auth"];

let auth: SupabaseAuth | undefined;

/**
 * Supabase is used for AUTH ONLY in the admin app. All data goes through the FastAPI backend
 * (TRD §1.3), so this module hands out `auth` and never the client itself. An ESLint rule
 * (apps/admin/eslint.config.js) also forbids `.from()`, `.rpc()` and `.storage`.
 *
 * Created on first use so the app (and its tests) still load when env vars are not set.
 */
export function getSupabaseAuth(): SupabaseAuth {
  if (!auth) {
    const url = import.meta.env.VITE_SUPABASE_URL;
    const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;
    if (!url || !anonKey) {
      throw new Error("VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY must be set (see apps/admin/.env.example)");
    }
    // Defaults: the session persists in localStorage and the access token refreshes itself.
    auth = createClient(url, anonKey, {
      auth: { persistSession: true, autoRefreshToken: true },
    }).auth;
  }
  return auth;
}
