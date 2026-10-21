import { Button } from "@delea/ui";
import { useState } from "react";
import { useAuth } from "./authContext";

/** Signed in, but the account belongs to no business (no tenant_id / app_role in the token). */
export function NoAccessPage() {
  const { state, signOut } = useAuth();
  const [busy, setBusy] = useState(false);
  const email = state.status === "signedIn" ? state.email : null;

  return (
    <main className="auth-page">
      <div className="auth-card">
        <h1>Your account has no access to a business yet</h1>
        <p>
          {email ? (
            <>
              You're signed in as <strong>{email}</strong>, but this account hasn't been added to a business.
            </>
          ) : (
            <>This account hasn't been added to a business.</>
          )}{" "}
          Ask the business owner to invite you, then sign in again.
        </p>
        <Button
          type="button"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            void signOut();
          }}
        >
          {busy ? "Signing out…" : "Sign out"}
        </Button>
      </div>
    </main>
  );
}
