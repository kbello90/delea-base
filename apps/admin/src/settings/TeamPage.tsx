import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { validateSignIn } from "../auth/signInValidation";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorState } from "../components/ErrorState";
import { Field } from "../components/Field";
import { PageHeader } from "../components/PageHeader";
import { useToast } from "../components/toastContext";
import { ROLE_LABEL } from "../routes/routeTable";
import { INVITABLE_ROLES, useChangeRole, useInvite, useRemoveMember, useResendInvite, useTeam, type InvitableRole, type Member } from "./teamApi";
import "./settings.css";

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong. Please try again.");

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

function InviteForm() {
  const toast = useToast();
  const invite = useInvite();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<InvitableRole>("staff");
  const [emailError, setEmailError] = useState<string | undefined>();

  function submit(event: FormEvent) {
    event.preventDefault();
    const error = validateSignIn(email, "x").email;
    setEmailError(error);
    if (error) return;
    invite.mutate(
      { email: email.trim(), app_role: role },
      {
        onSuccess: (member) => {
          setEmail("");
          toast({ title: `Invite sent to ${member.email}`, variant: "success" });
        },
      },
    );
  }

  return (
    <form className="record-form settings-card" onSubmit={submit} noValidate aria-labelledby="invite-heading">
      <h2 id="invite-heading">Invite someone</h2>
      <p className="field-hint">They get an email with a link to choose a password. The link works once and expires in 24 hours.</p>
      {invite.isError && (
        <p className="form-error" role="alert">
          {message(invite.error)}
        </p>
      )}
      <div className="form-grid">
        <Field id="invite-email" label="Email" error={emailError}>
          <input
            id="invite-email"
            type="email"
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={invite.isPending}
            aria-invalid={Boolean(emailError)}
            aria-describedby={emailError ? "invite-email-error" : undefined}
          />
        </Field>
        <Field id="invite-role" label="Access" hint={INVITABLE_ROLES.find((r) => r.value === role)?.hint}>
          <select id="invite-role" value={role} onChange={(e) => setRole(e.target.value as InvitableRole)} disabled={invite.isPending} aria-describedby="invite-role-hint">
            {INVITABLE_ROLES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="form-actions">
        <Button type="submit" disabled={invite.isPending} aria-busy={invite.isPending}>
          {invite.isPending ? "Sending…" : "Send invite"}
        </Button>
      </div>
    </form>
  );
}

function StatusTag({ member }: { member: Member }) {
  if (member.status === "active") return <span className="tag tag--ok">Active</span>;
  if (member.invite_expired) return <span className="tag tag--warn">Invite expired</span>;
  return <span className="tag">Invited</span>;
}

function MemberRow({ member, isMe, onRemove }: { member: Member; isMe: boolean; onRemove: (m: Member) => void }) {
  const toast = useToast();
  const changeRole = useChangeRole();
  const resend = useResendInvite();
  const editable = !isMe && member.app_role !== "owner";
  const error = changeRole.error ?? resend.error;

  return (
    <tr>
      <td>
        <span className="cell-title">{member.email}</span>
        {isMe && <span className="muted"> (you)</span>}
        {error && (
          <p className="field-error" role="alert">
            {message(error)}
          </p>
        )}
      </td>
      <td data-label="Access">
        {editable ? (
          <select
            aria-label={`Access for ${member.email}`}
            value={member.app_role}
            disabled={changeRole.isPending}
            onChange={(e) =>
              changeRole.mutate(
                { userId: member.user_id, role: e.target.value as InvitableRole },
                { onSuccess: (m) => toast({ title: `${m.email} now has ${ROLE_LABEL[m.app_role]} access`, variant: "success" }) },
              )
            }
          >
            {INVITABLE_ROLES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        ) : (
          ROLE_LABEL[member.app_role]
        )}
      </td>
      <td data-label="Status">
        <StatusTag member={member} />
      </td>
      <td data-label="Last sign-in">
        {member.last_sign_in_at ? dateFormat.format(new Date(member.last_sign_in_at)) : <span className="muted">Never</span>}
      </td>
      <td className="row-actions">
        {editable && member.status === "invited" && (
          <button
            type="button"
            className="btn-text"
            disabled={resend.isPending}
            aria-label={`Resend invite to ${member.email}`}
            onClick={() => resend.mutate(member.user_id, { onSuccess: () => toast({ title: `New invite sent to ${member.email}`, variant: "success" }) })}
          >
            {resend.isPending ? "Sending…" : "Resend invite"}
          </button>
        )}
        {editable && (
          <button type="button" className="btn-text btn-text--danger" aria-label={`Remove ${member.email}`} onClick={() => onRemove(member)}>
            Remove
          </button>
        )}
      </td>
    </tr>
  );
}

export function TeamPage() {
  const { state } = useAuth();
  const myId = state.status === "signedIn" ? state.userId : null;
  const toast = useToast();
  const team = useTeam();
  const remove = useRemoveMember();
  const [removing, setRemoving] = useState<Member | null>(null);

  return (
    <>
      <PageHeader
        title="Team"
        description="Invite staff and your accountant, and change or remove their access."
        actions={
          <Link to="/settings" className="btn-text">
            Back to settings
          </Link>
        }
      />
      <InviteForm />
      <section className="settings-card" aria-labelledby="members-heading">
        <h2 id="members-heading">People with access</h2>
        {team.isPending && <div className="skeleton skeleton--table" role="status" aria-label="Loading team" />}
        {team.isError && <ErrorState title="The team couldn't be loaded" error={team.error} onRetry={() => void team.refetch()} />}
        {team.data && (
          <div className="table-wrap" aria-busy={team.isFetching}>
            <table className="data-grid">
              <caption className="visually-hidden">People with access to this business</caption>
              <thead>
                <tr>
                  <th scope="col">Email</th>
                  <th scope="col">Access</th>
                  <th scope="col">Status</th>
                  <th scope="col">Last sign-in</th>
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {team.data.map((member) => (
                  <MemberRow key={member.user_id} member={member} isMe={member.user_id === myId} onRemove={setRemoving} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="field-hint">
          Each email address can belong to one business. An accountant who works for several businesses needs a separate address for
          each (for example name+yourbusiness@gmail.com).
        </p>
      </section>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRemoving(null);
            remove.reset();
          }
        }}
        title="Remove from the team?"
        description={
          <p>
            <strong>{removing?.email}</strong> loses access straight away and is signed out everywhere. You can invite them again later.
          </p>
        }
        confirmLabel="Remove"
        busyLabel="Removing…"
        danger
        busy={remove.isPending}
        error={remove.isError ? message(remove.error) : null}
        onConfirm={() =>
          removing &&
          remove.mutate(removing.user_id, {
            onSuccess: () => {
              toast({ title: `${removing.email} was removed`, variant: "success" });
              setRemoving(null);
            },
          })
        }
      />
    </>
  );
}
