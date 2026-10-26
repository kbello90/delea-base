import { createContext, useContext } from "react";
import type { LinkKind } from "./authBackend";
import type { UiClaims } from "./claims";

export type AuthState =
  | { status: "loading" }
  | { status: "signedOut" }
  | {
      status: "signedIn";
      /** The token's subject: changes when a different user signs in. */
      userId: string | null;
      email: string | null;
      /** From the token, for navigation only (see claims.ts). Null: no business membership. */
      claims: UiClaims | null;
    };

export interface AuthContextValue {
  state: AuthState;
  signIn(email: string, password: string): Promise<string | null>;
  /** Uses an invite or reset link's one-time token (signs in). A message on failure, or null. */
  verifyLink(kind: LinkKind, tokenHash: string): Promise<string | null>;
  /** Sets the signed-in user's password. A message on failure, or null. */
  setPassword(password: string): Promise<string | null>;
  signOut(): Promise<void>;
  /** The API said this account has no tenant membership (403); show the no-access page. */
  reportNoMembership(): void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
