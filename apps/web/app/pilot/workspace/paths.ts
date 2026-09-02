import {resolvePortfolioSite} from "../portfolio.mjs";

export const workspaceTabs = ["chat", "tasks", "skills", "reports"] as const;
export type WorkspaceTab = (typeof workspaceTabs)[number];

export function resolveTab(value: unknown): WorkspaceTab {
  return workspaceTabs.includes(value as WorkspaceTab) ? (value as WorkspaceTab) : "chat";
}

export function workspacePath(
  host: string,
  params: {tab?: WorkspaceTab; session?: string; error?: string} = {},
): string {
  const selected = resolvePortfolioSite(host);
  const query = new URLSearchParams({site: selected.host});
  if (params.tab) query.set("tab", params.tab);
  if (params.session) query.set("session", params.session);
  if (params.error) query.set("error", params.error);
  return `/pilot/workspace?${query.toString()}`;
}
