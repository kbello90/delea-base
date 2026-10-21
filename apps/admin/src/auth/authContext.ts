import { createContext, useContext } from "react";
import type { UiClaims } from "./claims";

export type AuthState =
  | { status: "loading" }
  | { status: "signedOut" }
  | {
      status: "signedIn";
      email: string | null;
      /** From the token, for navigation only (see claims.ts). Null: no business membership. */
      claims: UiClaims | null;
    };

export interface AuthContextValue {
  state: AuthState;
  signIn(email: string, password: string): Promise<string | null>;
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
