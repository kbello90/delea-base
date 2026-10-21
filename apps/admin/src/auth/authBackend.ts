import type { AuthHandlers } from "@delea/api-client";
import type { SupabaseAuth } from "../lib/supabaseClient";

export interface SessionInfo {
  accessToken: string;
  email: string | null;
}

/** Everything the app needs from an auth provider. Supabase is the only implementation; tests use fakes. */
export interface AuthBackend extends AuthHandlers {
  getSession(): Promise<SessionInfo | null>;
  /** Calls back on sign-in, sign-out and token refresh. Returns an unsubscribe function. */
  onChange(listener: (session: SessionInfo | null) => void): () => void;
  /** Resolves to a message for the user on failure, or null on success. */
  signIn(email: string, password: string): Promise<string | null>;
}

interface SupabaseSessionLike {
  access_token: string;
  user: { email?: string | null };
}

function toInfo(session: SupabaseSessionLike | null | undefined): SessionInfo | null {
  return session ? { accessToken: session.access_token, email: session.user.email ?? null } : null;
}

/** Turns a Supabase Auth error into a message for the sign-in form. */
export function signInMessage(error: { message: string; status?: number }): string {
  if (/invalid login credentials/i.test(error.message)) return "Email or password is incorrect.";
  if (/email not confirmed/i.test(error.message)) return "This email address hasn't been confirmed yet.";
  if (error.status === 0 || /fetch/i.test(error.message)) {
    return "Can't reach the sign-in service. Check your connection and try again.";
  }
  if (error.status === 429) return "Too many attempts. Wait a minute and try again.";
  return "Sign-in failed. Please try again.";
}

export function supabaseAuthBackend(auth: SupabaseAuth): AuthBackend {
  return {
    async getSession() {
      const { data } = await auth.getSession();
      return toInfo(data.session);
    },
    onChange(listener) {
      const { data } = auth.onAuthStateChange((_event, session) => listener(toInfo(session)));
      return () => data.subscription.unsubscribe();
    },
    async signIn(email, password) {
      try {
        const { error } = await auth.signInWithPassword({ email, password });
        return error ? signInMessage(error) : null;
      } catch {
        return signInMessage({ message: "fetch failed", status: 0 });
      }
    },
    async signOut() {
      // scope "local": end this browser's session even if the server can't be reached.
      await auth.signOut({ scope: "local" });
    },
    async getAccessToken() {
      const { data } = await auth.getSession();
      return data.session?.access_token ?? null;
    },
    async refresh() {
      const { data, error } = await auth.refreshSession();
      return error ? null : (data.session?.access_token ?? null);
    },
  };
}
