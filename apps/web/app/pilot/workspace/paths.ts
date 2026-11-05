import {safeHost} from "../site-selection.mjs";

export const workspaceTabs = ["chat", "reddit", "tasks", "skills", "reports"] as const;
export type WorkspaceTab = (typeof workspaceTabs)[number];

export function resolveTab(value: unknown): WorkspaceTab {
  return workspaceTabs.includes(value as WorkspaceTab) ? (value as WorkspaceTab) : "chat";
}

export function workspacePath(
  host: string,
  params: {tab?: WorkspaceTab; session?: string; error?: string; covered?: string} = {},
): string {
  const selected = safeHost(host);
  const query = new URLSearchParams();
  if (selected) query.set("site", selected);
  if (params.tab) query.set("tab", params.tab);
  if (params.session) query.set("session", params.session);
  if (params.error) query.set("error", params.error);
  // The site's own page a refused post topic is already covered by: a path only.
  if (params.covered && /^\/[A-Za-z0-9._~/-]{0,300}$/.test(params.covered)) query.set("covered", params.covered);
  return `/pilot/workspace?${query.toString()}`;
}
