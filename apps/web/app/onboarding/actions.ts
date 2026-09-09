"use server";

import {revalidatePath} from "next/cache";
import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

const PAGE = "/onboarding";

/**
 * Redirect, and invalidate what is being returned to.
 *
 * The same helper as every other action module, for the same reason: without
 * it the redirect is served from the client router cache, and a redirect to a
 * page whose data just changed shows the render from before the change. Here
 * that would be the worst version of it -- the workspace is created, and the
 * page still says you have none.
 */
function redirectFresh(path: string): never {
  revalidatePath("/", "layout");
  redirect(path);
}


/**
 * Create a workspace and become its owner.
 *
 * The first thing a new account does, and until it exists every other page
 * answers `no_tenant_membership`. There is deliberately no way to join somebody
 * else's workspace from here: that takes an invitation from a member of it.
 */
export async function createWorkspaceAction(formData: FormData): Promise<never> {
  const name = String(formData.get("name") ?? "").trim();
  if (name.length < 2) redirectFresh(`${PAGE}?error=tenant_name_invalid`);

  try {
    await apiJson("/v1/tenants", {method: "POST", body: JSON.stringify({name})});
  } catch (error) {
    unstable_rethrow(error);
    const code = error instanceof ApiError ? error.code : "unexpected-error";
    redirectFresh(`${PAGE}?error=${encodeURIComponent(code)}`);
  }
  // The whole layout, not just this page: every tenant-scoped route was last
  // rendered under "you are in no workspace" and would still say so.
  redirectFresh("/settings/keys?created=1");
}
