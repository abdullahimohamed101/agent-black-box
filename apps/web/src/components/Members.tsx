"use client";

import { useState } from "react";
import { settings } from "@/copy/settings";
import { fieldErrors } from "@/lib/api/client";
import { ROLES, type Role } from "@/lib/api/types";
import {
  useChangeRole,
  useInvitations,
  useInvite,
  useMembers,
  useRemoveMember,
  useRevokeInvitation,
} from "@/lib/admin";
import { ActionError, FieldError, Lede, Section, TwoStep, when } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";
import { useCan, useWorkspace } from "./WorkspaceProvider";

export function Members() {
  const ws = useWorkspace();
  const canWrite = useCan("member.write");
  const canWriteOwner = useCan("member.write_owner");
  const canSeeInvites = useCan("invite.read");
  const canInvite = useCan("invite.write");
  const members = useMembers();
  const invitations = useInvitations(canSeeInvites);
  const change = useChangeRole();
  const remove = useRemoveMember();
  const revoke = useRevokeInvitation();
  const c = settings.members;

  return (
    <>
      <Lede>{c.lede}</Lede>
      <Section title="Members">
        {members.isPending ? (
          <Loading label="Loading members" />
        ) : members.error ? (
          <ErrorState error={members.error} onRetry={() => void members.refetch()} />
        ) : members.data.length === 0 ? (
          <Empty title={c.empty} motif="none" />
        ) : (
          <div className="table-wrap">
            <table>
              <caption className="sr-only">{c.tableCaption}</caption>
              <thead>
                <tr>
                  <th>{c.heads.member}</th>
                  <th>{c.heads.role}</th>
                  <th>{c.heads.joined}</th>
                  {canWrite && <th>{c.heads.actions}</th>}
                </tr>
              </thead>
              <tbody>
                {members.data.map((m) => {
                  const self = m.user_id === ws.user?.id;
                  // Changing anyone who is, or becomes, an OWNER needs `member.write_owner` (the API enforces it).
                  const locked = m.role === "OWNER" && !canWriteOwner;
                  return (
                    <tr key={m.user_id}>
                      <td>
                        {m.name ? `${m.name} ` : ""}
                        <span className="muted">{m.email}</span>
                        {self && <span className="tag"> {c.you}</span>}
                      </td>
                      <td>
                        {canWrite && !locked ? (
                          <select
                            aria-label={`Role of ${m.email}`}
                            value={m.role}
                            disabled={change.isPending}
                            onChange={(e) =>
                              change.mutate({ userId: m.user_id, role: e.target.value as Role })
                            }
                          >
                            {ROLES.filter((r) => r !== "OWNER" || canWriteOwner).map((r) => (
                              <option key={r} value={r}>
                                {r}
                              </option>
                            ))}
                          </select>
                        ) : (
                          m.role
                        )}
                      </td>
                      <td>{when(m.joined_at)}</td>
                      {canWrite && (
                        <td>
                          {locked ? null : (
                            <TwoStep
                              label={settings.confirm.remove.first}
                              confirmLabel={settings.confirm.remove.second}
                              onConfirm={() => remove.mutate(m.user_id)}
                            />
                          )}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <ActionError error={change.error ?? remove.error} />
      </Section>

      {canSeeInvites && (
        <Section title={c.invitations.title} lede={c.invitations.lede}>
          {canInvite && <InviteForm canOwner={canWriteOwner} />}
          {invitations.isPending ? (
            <Loading label="Loading invitations" />
          ) : invitations.error ? (
            <ErrorState error={invitations.error} onRetry={() => void invitations.refetch()} />
          ) : invitations.data.length === 0 ? (
            <Empty title={c.invitations.empty} motif="none" />
          ) : (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">{c.invitations.caption}</caption>
                <thead>
                  <tr>
                    <th>{c.invitations.heads.email}</th>
                    <th>{c.invitations.heads.role}</th>
                    <th>{c.invitations.heads.expires}</th>
                    {canInvite && <th>{c.invitations.heads.actions}</th>}
                  </tr>
                </thead>
                <tbody>
                  {invitations.data.map((i) => (
                    <tr key={i.id}>
                      <td>{i.email}</td>
                      <td>{i.role}</td>
                      <td>{when(i.expires_at)}</td>
                      {canInvite && (
                        <td>
                          <TwoStep
                            label={settings.confirm.revoke.first}
                            confirmLabel={settings.confirm.revoke.second}
                            onConfirm={() => revoke.mutate(i.id)}
                          />
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <ActionError error={revoke.error} />
        </Section>
      )}
    </>
  );
}

function InviteForm({ canOwner }: { canOwner: boolean }) {
  const invite = useInvite();
  const c = settings.members.invitations;
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("VIEWER");
  const errors = fieldErrors(invite.error);
  return (
    <>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          invite.mutate({ email, role });
        }}
      >
        <label className="field">
          {c.email}
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <FieldError message={errors.email} />
        </label>
        <label className="field">
          {c.role}
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.filter((r) => r !== "OWNER" || canOwner).map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <FieldError message={errors.role} />
        </label>
        <button type="submit" className="primary" disabled={invite.isPending}>
          {invite.isPending ? c.creating : c.create}
        </button>
      </form>
      {Object.keys(errors).length === 0 && <ActionError error={invite.error} />}
      {invite.data && (
        <div role="status" className="withheld">
          <p>
            Invitation for <strong>{invite.data.invitation.email}</strong> created. Copy the link
            now: it is shown once and cannot be retrieved later.
          </p>
          <p className="secret" data-testid="invite-link">
            {invite.data.link}
          </p>
          <button type="button" onClick={() => invite.reset()}>
            {c.done}
          </button>
        </div>
      )}
    </>
  );
}
