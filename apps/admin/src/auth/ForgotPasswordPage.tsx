import { ApiError, unwrap } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../lib/api";
import { AuthCard } from "./SetPasswordPage";
import { validateSignIn } from "./signInValidation";

/**
 * Asks the backend (POST /auth/password-reset) to email a reset link. The answer is the same
 * whether or not the address has an account, so this page never says which.
 */
export function ForgotPasswordPage() {
  const api = useApi();
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState<string | undefined>();
  const request = useMutation({
    mutationFn: (address: string) => unwrap(api.POST("/api/v1/auth/password-reset", { body: { email: address } })),
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const error = validateSignIn(email, "x").email;
    setEmailError(error);
    if (!error) request.mutate(email.trim());
  }

  if (request.isSuccess) {
    return (
      <AuthCard title="Check your email">
        <p role="status">
          If an account exists for <strong>{request.variables}</strong>, we've sent a link to reset the password. The link
          works once and expires in 24 hours.
        </p>
        <p className="muted">No email after a few minutes? Check your spam folder, or ask your business owner.</p>
        <p className="auth-alt">
          <Link to="/sign-in">Back to sign in</Link>
        </p>
      </AuthCard>
    );
  }

  const failure =
    request.error instanceof ApiError ? request.error.message : request.error ? "Something went wrong. Please try again." : null;

  return (
    <AuthCard title="Reset your password">
      <p className="muted">Enter the email address you sign in with. We'll email you a link to choose a new password.</p>
      <form onSubmit={onSubmit} noValidate aria-describedby={failure ? "forgot-error" : undefined}>
        {failure && (
          <p id="forgot-error" className="form-error" role="alert">
            {failure}
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
            aria-invalid={Boolean(emailError)}
            aria-describedby={emailError ? "email-error" : undefined}
            disabled={request.isPending}
          />
          {emailError && (
            <span id="email-error" className="field-error">
              {emailError}
            </span>
          )}
        </div>
        <Button type="submit" className="auth-submit" disabled={request.isPending} aria-busy={request.isPending}>
          {request.isPending ? "Sending…" : "Send reset link"}
        </Button>
      </form>
      <p className="auth-alt">
        <Link to="/sign-in">Back to sign in</Link>
      </p>
    </AuthCard>
  );
}
