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
  /**
   * Uses the one-time token from an invite or password-reset email (signs the user in).
   * Resolves to a message for the user on failure, or null on success.
   */
  verifyLink(kind: LinkKind, tokenHash: string): Promise<string | null>;
  /** Sets the signed-in user's password. A message on failure, or null. */
  setPassword(password: string): Promise<string | null>;
}

/** The emails Auth sends: an owner's invite, or a password reset. */
export type LinkKind = "invite" | "recovery";

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

const CANT_REACH = "Can't reach the sign-in service. Check your connection and try again.";

/** Turns a Supabase Auth error from an email link into a message for the page. */
export function linkMessage(kind: LinkKind, error: { message: string; status?: number; code?: string }): string {
  if (error.code === "otp_expired" || error.status === 403) {
    return kind === "invite"
      ? "This invite link has expired or was already used. Ask your business owner to send a new one."
      : "This reset link has expired or was already used. Ask for a new one below.";
  }
  if (error.status === 0 || /fetch/i.test(error.message)) return CANT_REACH;
  if (error.status === 429) return "Too many attempts. Wait a minute and try again.";
  return "Something went wrong. Please try again.";
}

/** Turns a Supabase Auth error from setting a password into a message for the form. */
export function passwordMessage(error: { message: string; status?: number; code?: string }): string {
  if (error.code === "weak_password") return "Choose a longer password: at least 10 characters.";
  if (error.code === "same_password") return "Choose a password you haven't used for this account.";
  if (error.status === 0 || /fetch/i.test(error.message)) return CANT_REACH;
  return "Your password couldn't be saved. Please try again.";
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
    async verifyLink(kind, tokenHash) {
      try {
        const { error } = await auth.verifyOtp({ type: kind, token_hash: tokenHash });
        return error ? linkMessage(kind, error) : null;
      } catch {
        return CANT_REACH;
      }
    },
    async setPassword(password) {
      try {
        const { error } = await auth.updateUser({ password });
        return error ? passwordMessage(error) : null;
      } catch {
        return CANT_REACH;
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
