import { Button } from "@delea/ui";
import { useAuth } from "../auth/authContext";

/** Temporary landing page until the app shell arrives. */
export function SignedInHome() {
  const { state, signOut } = useAuth();
  if (state.status !== "signedIn") return null;
  return (
    <main className="auth-page">
      <div className="auth-card">
        <h1>Signed in</h1>
        <p>
          {state.email} ({state.claims?.appRole})
        </p>
        <Button type="button" onClick={() => void signOut()}>
          Sign out
        </Button>
      </div>
    </main>
  );
}
