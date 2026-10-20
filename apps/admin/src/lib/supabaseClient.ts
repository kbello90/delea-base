import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | undefined;

/**
 * Lazily creates the Supabase browser client (auth + RLS-scoped reads, TRD §1.3).
 * Created on first use so the app still boots when env vars are not set.
 */
export function getSupabase(): SupabaseClient {
  if (!client) {
    const url = import.meta.env.VITE_SUPABASE_URL;
    const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;
    if (!url || !anonKey) {
      throw new Error("VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY must be set");
    }
    client = createClient(url, anonKey);
  }
  return client;
}
