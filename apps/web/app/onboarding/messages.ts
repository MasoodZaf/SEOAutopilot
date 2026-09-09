/**
 * What each refusal from the tenant route means, in words.
 *
 * Separate from `actions.ts` because that module is `"use server"`, where a
 * non-async export is a build error.
 */
const MESSAGES: Record<string, string> = {
  tenant_name_invalid: "Give the workspace a name of at least two characters.",
  tenant_limit_reached: "You already own the maximum number of workspaces.",
  tenant_slug_unavailable: "That name could not be made into a workspace address. Try another.",
};

export function messageFor(code: string | undefined): string | null {
  if (!code) return null;
  return MESSAGES[code] ?? "The workspace could not be created. Try again.";
}
