"use server";

import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

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
  redirect(outcome({error: error instanceof ApiError ? error.code : "unexpected-error"}));
}

function required(formData: FormData, field: string): string {
  const value = formData.get(field);
  return typeof value === "string" ? value.trim() : "";
}

export async function inviteMemberAction(formData: FormData): Promise<never> {
  const email = required(formData, "email");
  const role = required(formData, "role");
  if (!email || !role) redirect(outcome({error: "email_and_role_required"}));
  try {
    await apiJson("/v1/members/invitations", {
      method: "POST",
      body: JSON.stringify({email, role}),
    });
  } catch (error) {
    failure(error);
  }
  redirect(outcome({invited: email}));
}

export async function revokeInvitationAction(formData: FormData): Promise<never> {
  const id = required(formData, "invitation_id");
  if (!id) redirect(outcome({error: "invitation_required"}));
  try {
    await apiJson(`/v1/members/invitations/${id}`, {method: "DELETE"});
  } catch (error) {
    failure(error);
  }
  redirect(outcome({revoked: "1"}));
}

export async function changeRoleAction(formData: FormData): Promise<never> {
  const id = required(formData, "membership_id");
  const role = required(formData, "role");
  if (!id || !role) redirect(outcome({error: "membership_and_role_required"}));
  try {
    await apiJson(`/v1/members/${id}`, {
      method: "PATCH",
      body: JSON.stringify({role}),
    });
  } catch (error) {
    failure(error);
  }
  redirect(outcome({updated: "1"}));
}

export async function removeMemberAction(formData: FormData): Promise<never> {
  const id = required(formData, "membership_id");
  if (!id) redirect(outcome({error: "membership_required"}));
  try {
    await apiJson(`/v1/members/${id}`, {method: "DELETE"});
  } catch (error) {
    failure(error);
  }
  redirect(outcome({removed: "1"}));
}
