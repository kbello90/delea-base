import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { canVisit } from "../routes/routeTable";
import { DEFAULT_BRAND } from "../theme/defaults";
import { useAuth } from "./authContext";
import { validateSignIn, type FieldErrors } from "./signInValidation";

export function SignInPage() {
  const { state, signIn } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (state.status === "signedIn") {
    // Back to the page that sent us here only if this account may see it (the previous user
    // may have had another role); otherwise "/" picks the role's start page.
    const role = state.claims?.appRole;
    return <Navigate to={role && canVisit(role, from) ? from : "/"} replace />;
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const errors = validateSignIn(email, password);
    setFieldErrors(errors);
    if (errors.email || errors.password) return;

    setSubmitting(true);
    const message = await signIn(email.trim(), password);
    setSubmitting(false);
    // On success the session change re-renders this page, which redirects (above).
    if (message) setFormError(message);
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand" aria-hidden="true">
          <span className="brand-mark">{DEFAULT_BRAND.businessName.charAt(0)}</span>
          <span className="brand-name">{DEFAULT_BRAND.businessName}</span>
        </div>
        <h1>Sign in</h1>
        <p className="muted">Use the email and password for your business account.</p>

        <form onSubmit={onSubmit} noValidate aria-describedby={formError ? "signin-error" : undefined}>
          {formError && (
            <p id="signin-error" className="form-error" role="alert">
              {formError}
            </p>
          )}
          <div className="field">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-invalid={Boolean(fieldErrors.email)}
              aria-describedby={fieldErrors.email ? "email-error" : undefined}
              disabled={submitting}
            />
            {fieldErrors.email && (
              <span id="email-error" className="field-error">
                {fieldErrors.email}
              </span>
            )}
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={Boolean(fieldErrors.password)}
              aria-describedby={fieldErrors.password ? "password-error" : undefined}
              disabled={submitting}
            />
            {fieldErrors.password && (
              <span id="password-error" className="field-error">
                {fieldErrors.password}
              </span>
            )}
          </div>
          <Button type="submit" className="auth-submit" disabled={submitting} aria-busy={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </div>
    </main>
  );
}
