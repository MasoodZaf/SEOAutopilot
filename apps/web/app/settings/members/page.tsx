import type {Metadata} from "next";

import {cn} from "@/lib/cn";
import {redirect} from "next/navigation";

import {ApiError, apiJson, currentSession, isMissingTenant} from "@/lib/server-api";

import {
  changeRoleAction,
  inviteMemberAction,
  removeMemberAction,
  revokeInvitationAction,
} from "./actions";

export const metadata: Metadata = {
  title: "Members",
  robots: {index: false, follow: false},
};

type Member = {
  id: string;
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  status: string;
  created_at: string;
};

type Invitation = {
  id: string;
  email_normalized: string;
  role: string;
  expires_at: string;
  created_at: string;
};

const ROLES = ["owner", "admin", "seo_manager", "editor", "developer", "viewer"] as const;

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  seo_manager: "SEO manager",
  editor: "Editor",
  developer: "Developer",
  viewer: "Viewer",
};

/**
 * The refusals worth explaining rather than echoing.
 *
 * These come back from the API, which is where the rules are enforced. Saying
 * *why* matters more than usual here: "only an owner grants ownership" and
 * "this would leave the tenant with no owner" look like bugs if you only see a
 * failure.
 */
const REASONS: Record<string, string> = {
  only_an_owner_grants_ownership:
    "Only an owner can make someone else an owner. Ask an owner to do it.",
  tenant_would_have_no_owner:
    "That would leave this tenant with no owner and nobody able to administer it. Make somebody else an owner first.",
  only_an_owner_changes_an_owner:
    "Only an owner can change or remove another owner.",
  cannot_change_own_membership:
    "You cannot change your own membership. Ask another owner or admin.",
  already_a_member: "That address already belongs to somebody in this tenant.",
  invitation_already_open: "That address already has an open invitation.",
  email_invalid: "That does not look like an email address.",
  membership_not_found: "That member is no longer in this tenant.",
  invitation_not_found: "That invitation no longer exists.",
  forbidden: "Your role does not allow changing who is in this tenant.",
  inviter_is_not_a_signed_in_user:
    "This deployment is still using the operator token rather than a signed-in identity, so there is nobody to record as the inviter. The first owner is invited from the host with app.cli.bootstrap_owner.",
  email_and_role_required: "An address and a role are both required.",
};

function formatDate(value: string): string {
  return new Date(value).toISOString().slice(0, 10);
}

export default async function MembersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const query = await searchParams;
  const session = await currentSession();

  let members: Member[] = [];
  let invitations: Invitation[] = [];
  let loadError: string | null = null;
  try {
    [members, invitations] = await Promise.all([
      apiJson<{data: Member[]}>("/v1/members").then((body) => body.data),
      apiJson<{data: Invitation[]}>("/v1/members/invitations").then((body) => body.data),
    ]);
  } catch (error) {
    // A brand-new account is in no workspace yet. Rendering that as a load
    // failure shows a first-time visitor the string `no_tenant_membership`,
    // which names nothing they can act on; the page that fixes it does.
    if (isMissingTenant(error)) redirect("/onboarding");
    loadError = error instanceof ApiError ? error.code : "unexpected-error";
  }

  const confirming = query.confirm_remove;
  const notice = query.invited
    ? `Invited ${query.invited}. They join when they first sign in.`
    : query.revoked
      ? "Invitation revoked."
      : query.updated
        ? "Role updated."
        : query.removed
          ? "Member removed. Their past work still names them."
          : null;
  const problem = query.error ? (REASONS[query.error] ?? query.error) : null;

  return (
    <main>
      <div className="mx-auto max-w-3xl px-6 py-12">
        <p className="text-sm font-semibold text-good">SEO Autopilot</p>
        <h1 className="mt-2 text-balance text-3xl font-semibold text-ink">Members</h1>
        <p className="mt-2 max-w-prose text-pretty text-sm text-ink-soft">
          A verified sign-in proves who somebody is. Membership is what decides whose data they
          may act on, and it only ever comes from an invitation.
        </p>

        {notice ? (
          <p
            role="status"
            className="mt-6 rounded-[4px] border border-good-rule bg-good-soft px-4 py-3 text-sm text-good"
          >
            {notice}
          </p>
        ) : null}
        {problem ? (
          <p
            role="alert"
            className="mt-6 rounded-[4px] border border-stop-rule bg-stop-soft px-4 py-3 text-sm text-stop"
          >
            {problem}
          </p>
        ) : null}

        {loadError ? (
          <section className="mt-8 rounded-xl border border-rule bg-surface p-6 shadow-sm">
            <h2 className="text-balance text-xl font-semibold text-ink">
              Members are not available
            </h2>
            <p className="mt-2 text-pretty text-sm text-ink-soft">
              {loadError === "authentication_not_configured"
                ? "This deployment has no identity provider configured, so there are no members to manage yet. An operator sets OIDC_ISSUER_URL and the first owner is invited from the host."
                : `The API refused the request: ${loadError}.`}
            </p>
          </section>
        ) : (
          <>
            <section
              aria-labelledby="members-heading"
              className="mt-8 rounded-xl border border-rule bg-surface p-6 shadow-sm"
            >
              <h2 id="members-heading" className="text-balance text-xl font-semibold text-ink">
                In this tenant
              </h2>
              {members.length === 0 ? (
                <p className="mt-2 text-pretty text-sm text-ink-soft">
                  Nobody yet. Invite the first person below.
                </p>
              ) : (
                <ul className="mt-4 divide-y divide-rule">
                  {members.map((member) => {
                    const isSelf = session?.email
                      ? session.email.toLowerCase() === member.email.toLowerCase()
                      : false;
                    return (
                      <li key={member.id} className="flex flex-wrap items-center gap-3 py-3">
                        <div className="min-w-0 grow">
                          <p className="truncate text-sm font-medium text-ink">
                            {member.email}
                            {isSelf ? (
                              <span className="ml-2 text-xs font-normal text-ink-faint">(you)</span>
                            ) : null}
                          </p>
                          <p className="text-xs text-ink-faint">
                            {member.display_name || "—"} · joined {formatDate(member.created_at)}
                            {member.status !== "active" ? ` · ${member.status}` : ""}
                          </p>
                        </div>

                        <form action={changeRoleAction} className="flex items-center gap-2">
                          <input type="hidden" name="membership_id" value={member.id} />
                          <label className="sr-only" htmlFor={`role-${member.id}`}>
                            Role for {member.email}
                          </label>
                          <select
                            id={`role-${member.id}`}
                            name="role"
                            defaultValue={member.role}
                            disabled={isSelf}
                            className="rounded-[4px] border border-rule-strong bg-surface px-2 py-1 text-sm text-ink disabled:bg-sunk disabled:text-ink-faint"
                          >
                            {ROLES.map((role) => (
                              <option key={role} value={role}>
                                {ROLE_LABEL[role]}
                              </option>
                            ))}
                          </select>
                          <button
                            type="submit"
                            disabled={isSelf}
                            className="rounded-[4px] border border-rule-strong bg-surface px-2.5 py-1 text-sm font-medium text-ink hover:bg-sunk disabled:cursor-not-allowed disabled:text-ink-faint"
                          >
                            Save
                          </button>
                        </form>

                        {confirming === member.id ? (
                          <span className="flex items-center gap-2">
                            <form action={removeMemberAction}>
                              <input type="hidden" name="membership_id" value={member.id} />
                              <button
                                type="submit"
                                className="rounded-[4px] border border-stop-rule bg-stop px-2.5 py-1 text-sm font-medium text-stop-ink hover:bg-stop"
                               data-tip="Remove this person from the workspace. Their account survives; their access here does not.">
                                Confirm removal
                              </button>
                            </form>
                            <a
                              href="/settings/members"
                              className="text-sm font-medium text-ink-soft underline underline-offset-2"
                            >
                              Cancel
                            </a>
                          </span>
                        ) : (
                          <a
                            href={`/settings/members?confirm_remove=${member.id}`}
                            className={cn(
                              "rounded-[4px] border px-2.5 py-1 text-sm font-medium",
                              isSelf
                                ? "pointer-events-none border-rule text-ink-faint"
                                : "border-stop-rule bg-surface text-stop hover:bg-stop-soft",
                            )}
                            aria-disabled={isSelf}
                          >
                            Remove
                          </a>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            <section
              aria-labelledby="invitations-heading"
              className="mt-6 rounded-xl border border-rule bg-surface p-6 shadow-sm"
            >
              <h2
                id="invitations-heading"
                className="text-balance text-xl font-semibold text-ink"
              >
                Invite somebody
              </h2>
              <p className="mt-2 text-pretty text-sm text-ink-soft">
                The invitation is claimed the first time that address signs in, and only if the
                identity provider says the address is verified.
              </p>
              <form action={inviteMemberAction} className="mt-4 flex flex-wrap items-end gap-3">
                <div className="grow">
                  <label htmlFor="invite-email" className="block text-sm font-medium text-ink">
                    Email address
                  </label>
                  <input
                    id="invite-email"
                    name="email"
                    type="email"
                    required
                    autoComplete="off"
                    placeholder="colleague@example.com"
                    className="mt-1 w-full rounded-[4px] border border-rule-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-faint"
                  />
                </div>
                <div>
                  <label htmlFor="invite-role" className="block text-sm font-medium text-ink">
                    Role
                  </label>
                  <select
                    id="invite-role"
                    name="role"
                    defaultValue="viewer"
                    className="mt-1 rounded-[4px] border border-rule-strong bg-surface px-2 py-2 text-sm text-ink"
                  >
                    {ROLES.map((role) => (
                      <option key={role} value={role}>
                        {ROLE_LABEL[role]}
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  type="submit"
                  className="rounded-[4px] bg-surface px-4 py-2 text-sm font-medium text-ink hover:bg-sunk"
                 data-tip="Invite this address into this workspace. They join on their next sign-in and can see everything in it.">
                  Send invitation
                </button>
              </form>

              {invitations.length > 0 ? (
                <ul className="mt-6 divide-y divide-rule border-t border-rule">
                  {invitations.map((invitation) => (
                    <li
                      key={invitation.id}
                      className="flex flex-wrap items-center gap-3 py-3"
                    >
                      <div className="min-w-0 grow">
                        <p className="truncate text-sm font-medium text-ink">
                          {invitation.email_normalized}
                        </p>
                        <p className="text-xs text-ink-faint">
                          {ROLE_LABEL[invitation.role] ?? invitation.role} · expires{" "}
                          {formatDate(invitation.expires_at)}
                        </p>
                      </div>
                      <form action={revokeInvitationAction}>
                        <input type="hidden" name="invitation_id" value={invitation.id} />
                        <button
                          type="submit"
                          className="rounded-[4px] border border-rule-strong bg-surface px-2.5 py-1 text-sm font-medium text-ink hover:bg-sunk"
                         data-tip="Cancel this invitation. The address can no longer join with it.">
                          Revoke
                        </button>
                      </form>
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          </>
        )}
      </div>
    </main>
  );
}
