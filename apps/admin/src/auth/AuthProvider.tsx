import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import type { AuthBackend, SessionInfo } from "./authBackend";
import { AuthContext, type AuthContextValue, type AuthState } from "./authContext";
import { readUiClaims } from "./claims";

function toState(session: SessionInfo | null, noMembership: boolean): AuthState {
  if (!session) return { status: "signedOut" };
  return {
    status: "signedIn",
    email: session.email,
    claims: noMembership ? null : readUiClaims(session.accessToken),
  };
}

export function AuthProvider({ backend, children }: { backend: AuthBackend; children: ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null | undefined>(undefined);
  const [noMembership, setNoMembership] = useState(false);

  useEffect(() => {
    let active = true;
    const unsubscribe = backend.onChange((next) => {
      if (!active) return;
      setSession(next);
      if (!next) setNoMembership(false);
    });
    backend
      .getSession()
      .then((initial) => active && setSession((current) => (current === undefined ? initial : current)))
      .catch(() => active && setSession(null));
    return () => {
      active = false;
      unsubscribe();
    };
  }, [backend]);

  const signOut = useCallback(async () => {
    await backend.signOut();
    setSession(null);
    setNoMembership(false);
  }, [backend]);

  const value = useMemo<AuthContextValue>(
    () => ({
      state: session === undefined ? { status: "loading" } : toState(session, noMembership),
      signIn: (email, password) => backend.signIn(email, password),
      signOut,
      reportNoMembership: () => setNoMembership(true),
    }),
    [backend, session, noMembership, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
