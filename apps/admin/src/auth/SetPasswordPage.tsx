import { Button } from "@delea/ui";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useToast } from "../components/toastContext";
import { DEFAULT_BRAND } from "../theme/defaults";
import type { LinkKind } from "./authBackend";
import { useAuth } from "./authContext";
import { MIN_PASSWORD_LENGTH, validateNewPassword, type PasswordErrors } from "./passwordForm";

const COPY: Record<LinkKind, { title: string; intro: string; submit: string; busy: string; done: string }> = {
  invite: {
    title: "Accept your invite",
    intro: "Choose a password for your account. You'll use it with this email address to sign in.",
    submit: "Accept and sign in",
    busy: "Setting up…",
    done: "Welcome! Your account is ready.",
  },
  recovery: {
    title: "Choose a new password",
    intro: "Enter a new password for your account.",
    submit: "Save password and sign in",
    busy: "Saving…",
    done: "Your password was changed.",
  },
};

/**
 * Where the invite and password-reset emails link to (supabase/templates). The one-time token
 * in the link is used only when the form is submitted, so an email scanner that opens the page
 * can't use it up. Nothing here is behind sign-in.
 */
function SetPasswordPage({ kind }: { kind: LinkKind }) {
  const { verifyLink, setPassword } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const [params] = useSearchParams();
  const tokenHash = params.get("token_hash");
  const copy = COPY[kind];

  const [password, setPasswordValue] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<PasswordErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [linkFailed, setLinkFailed] = useState(false);
  // The token works once: after it's used, a retry only sets the password again.
  const [verified, setVerified] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  if (!tokenHash || params.get("type") !== kind) {
    return (
      <AuthCard title={copy.title}>
        <p className="form-error" role="alert">
          This link is incomplete. Open the link from your email again.
        </p>
        <AltLinks kind={kind} />
      </AuthCard>
    );
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const found = validateNewPassword(password, confirm);
    setErrors(found);
    if (found.password || found.confirm) return;

    setSubmitting(true);
    if (!verified) {
      const failure = await verifyLink(kind, tokenHash!);
      if (failure) {
        setSubmitting(false);
        setLinkFailed(true);
        return setFormError(failure);
      }
      setVerified(true);
    }
    const failure = await setPassword(password);
    setSubmitting(false);
    if (failure) return setFormError(failure);
    toast({ title: copy.done, variant: "success" });
    navigate("/", { replace: true });
  }

  return (
    <AuthCard title={copy.title}>
      <p className="muted">{copy.intro}</p>
      <form onSubmit={onSubmit} noValidate aria-describedby={formError ? "set-password-error" : undefined}>
        {formError && (
          <p id="set-password-error" className="form-error" role="alert">
            {formError}
          </p>
        )}
        <div className="field">
          <label htmlFor="new-password">New password</label>
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPasswordValue(e.target.value)}
            aria-invalid={Boolean(errors.password)}
            aria-describedby={errors.password ? "new-password-error" : "new-password-hint"}
            disabled={submitting || linkFailed}
          />
          {errors.password ? (
            <span id="new-password-error" className="field-error">
              {errors.password}
            </span>
          ) : (
            <span id="new-password-hint" className="field-hint">
              At least {MIN_PASSWORD_LENGTH} characters.
            </span>
          )}
        </div>
        <div className="field">
          <label htmlFor="confirm-password">Repeat the password</label>
          <input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            aria-invalid={Boolean(errors.confirm)}
            aria-describedby={errors.confirm ? "confirm-password-error" : undefined}
            disabled={submitting || linkFailed}
          />
          {errors.confirm && (
            <span id="confirm-password-error" className="field-error">
              {errors.confirm}
            </span>
          )}
        </div>
        <Button type="submit" className="auth-submit" disabled={submitting || linkFailed} aria-busy={submitting}>
          {submitting ? copy.busy : copy.submit}
        </Button>
      </form>
      {linkFailed && <AltLinks kind={kind} />}
    </AuthCard>
  );
}

function AltLinks({ kind }: { kind: LinkKind }) {
  return (
    <p className="auth-alt">
      {kind === "recovery" && (
        <>
          <Link to="/forgot-password">Send a new reset link</Link>
          {" · "}
        </>
      )}
      <Link to="/sign-in">Back to sign in</Link>
    </p>
  );
}

export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand" aria-hidden="true">
          <span className="brand-mark">{DEFAULT_BRAND.businessName.charAt(0)}</span>
          <span className="brand-name">{DEFAULT_BRAND.businessName}</span>
        </div>
        <h1>{title}</h1>
        {children}
      </div>
    </main>
  );
}

export function AcceptInvitePage() {
  return <SetPasswordPage kind="invite" />;
}

export function ResetPasswordPage() {
  return <SetPasswordPage kind="recovery" />;
}
