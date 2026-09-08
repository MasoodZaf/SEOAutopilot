"use server";

import {revalidatePath} from "next/cache";
import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

/**
 * Redirect, and invalidate the page being returned to.
 *
 * Without this the redirect is served from the client router cache, and a
 * redirect to the URL already on screen is a no-op navigation -- the mutation
 * lands on the API and the page silently keeps showing what it showed before.
 * See the same helper in app/pilot/actions.ts for what that cost.
 */
function redirectFresh(path: string): never {
  revalidatePath("/settings/members", "page");
  redirect(path);
}


const PAGE = "/settings/members";

/**
 * Every rule that matters here lives in the API — an admin cannot create an
 * owner, a tenant cannot lose its last one, nobody edits their own membership.
 * These actions carry the refusal back to the page rather than pre-judging it,
 * so the UI cannot drift from what is actually enforced.
 */
function outcome(params: Record<string, string>): string {
  const query = new URLSearchParams(params);
  return `${PAGE}?${query.toString()}`;
}

function failure(error: unknown): never {
  unstable_rethrow(error);
  redirectFresh(outcome({error: error instanceof ApiError ? error.code : "unexpected-error"}));
}

function required(formData: FormData, field: string): string {
  const value = formData.get(field);
  return typeof value === "string" ? value.trim() : "";
}

export async function inviteMemberAction(formData: FormData): Promise<never> {
  const email = required(formData, "email");
  const role = required(formData, "role");
  if (!email || !role) redirectFresh(outcome({error: "email_and_role_required"}));
  try {
    await apiJson("/v1/members/invitations", {
      method: "POST",
      body: JSON.stringify({email, role}),
    });
  } catch (error) {
    failure(error);
  }
  redirectFresh(outcome({invited: email}));
}

export async function revokeInvitationAction(formData: FormData): Promise<never> {
  const id = required(formData, "invitation_id");
  if (!id) redirectFresh(outcome({error: "invitation_required"}));
  try {
    await apiJson(`/v1/members/invitations/${id}`, {method: "DELETE"});
  } catch (error) {
    failure(error);
  }
  redirectFresh(outcome({revoked: "1"}));
}

export async function changeRoleAction(formData: FormData): Promise<never> {
  const id = required(formData, "membership_id");
  const role = required(formData, "role");
  if (!id || !role) redirectFresh(outcome({error: "membership_and_role_required"}));
  try {
    await apiJson(`/v1/members/${id}`, {
      method: "PATCH",
      body: JSON.stringify({role}),
    });
  } catch (error) {
    failure(error);
  }
  redirectFresh(outcome({updated: "1"}));
}

export async function removeMemberAction(formData: FormData): Promise<never> {
  const id = required(formData, "membership_id");
  if (!id) redirectFresh(outcome({error: "membership_required"}));
  try {
    await apiJson(`/v1/members/${id}`, {method: "DELETE"});
  } catch (error) {
    failure(error);
  }
  redirectFresh(outcome({removed: "1"}));
}
