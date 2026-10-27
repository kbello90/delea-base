import { ApiError, unwrap } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useMutation } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useApi } from "../lib/api";
import { AuthCard } from "./SetPasswordPage";

/**
 * Public: the link in a reply-to confirmation email. Confirming needs a click on purpose: some
 * email security scanners open links by themselves, and that must not count as consent.
 */
export function ConfirmReplyToPage() {
  const api = useApi();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const confirm = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/email/confirm-reply-to", { body: { token } })),
  });

  if (confirm.isSuccess) {
    return (
      <AuthCard title="Address confirmed">
        <p role="status">
          Replies to <strong>{confirm.data.shop}</strong>&apos;s emails will now come to this address. You can close this page.
        </p>
      </AuthCard>
    );
  }
  const failure = confirm.error instanceof ApiError ? confirm.error.message : confirm.error ? "Something went wrong. Please try again." : null;
  return (
    <AuthCard title="Confirm this reply address">
      <p className="muted">A shop on DELEA asked for customers&apos; replies to come to this email address. Confirm only if that is you.</p>
      {!token && (
        <p className="form-error" role="alert">
          This link is incomplete. Open it again from the email.
        </p>
      )}
      {failure && (
        <p className="form-error" role="alert">
          {failure}
        </p>
      )}
      <Button type="button" className="auth-submit" disabled={!token || confirm.isPending} onClick={() => confirm.mutate()}>
        {confirm.isPending ? "Confirming…" : "Confirm this address"}
      </Button>
    </AuthCard>
  );
}
