"use server";

import {revalidatePath} from "next/cache";
import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

import {pilotPath, safeHost} from "../site-selection.mjs";
import type {AgentSession} from "./model";
import {resolveTab, workspacePath} from "./paths";

/**
 * Redirect, and invalidate the workspace page being returned to.
 *
 * This module had the sharpest form of the defect. Sending a chat message
 * redirects to `workspacePath(host, {tab: "chat", session})` -- the URL the
 * message was typed on -- so without invalidating first, the second message in
 * a session reached the API and never appeared in the transcript. The reply
 * existed; the page was showing a cached render from before it was sent.
 */
function redirectFresh(path: string): never {
  // "layout", so a brief's own page under /pilot/workspace/briefs is
  // invalidated along with the workspace list that shows its status.
  revalidatePath("/pilot/workspace", "layout");
  redirect(path);
}

type SiteCollection = {data: Array<{id: string; normalized_host: string}>};
type SessionEnvelope = {data: AgentSession};

/** Bounded, non-reflective error codes only. */
function safeCode(code: string): string {
  return /^[a-z0-9_-]+$/i.test(code) ? code : "unexpected-error";
}

async function siteIdFor(host: string): Promise<string | undefined> {
  const collection = await apiJson<SiteCollection>("/v1/sites");
  return collection.data.find((site) => site.normalized_host === host)?.id;
}

function formHost(formData: FormData): string {
  return safeHost(formData.get("site_host"));
}

export async function startConversation(formData: FormData): Promise<never> {
  const host = formHost(formData);
  const rawTitle = formData.get("title");
  const title = typeof rawTitle === "string" && rawTitle.trim() ? rawTitle.trim() : "New conversation";
  const siteId = await siteIdFor(host);
  if (!siteId) redirectFresh(workspacePath(host, {error: "site_not_onboarded"}));
  try {
    const created = await apiJson<SessionEnvelope>(`/v1/sites/${siteId}/agent-sessions`, {
      method: "POST",
      body: JSON.stringify({title: title.slice(0, 200)}),
    });
    redirectFresh(workspacePath(host, {tab: "chat", session: created.data.id}));
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    if (error instanceof ApiError) redirectFresh(workspacePath(host, {error: safeCode(error.code)}));
    throw error;
  }
}

export async function sendMessage(formData: FormData): Promise<never> {
  const host = formHost(formData);
  const sessionId = String(formData.get("session_id") ?? "");
  const body = String(formData.get("body") ?? "").trim();
  const skillKey = formData.get("skill_key");
  if (!sessionId || !body) redirectFresh(workspacePath(host, {tab: "chat", session: sessionId}));
  try {
    await apiJson(`/v1/agent-sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        body: body.slice(0, 8000),
        skill_key: typeof skillKey === "string" && skillKey ? skillKey : null,
      }),
    });
    redirectFresh(workspacePath(host, {tab: "chat", session: sessionId}));
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    if (error instanceof ApiError) {
      redirectFresh(workspacePath(host, {tab: "chat", session: sessionId, error: safeCode(error.code)}));
    }
    throw error;
  }
}

export async function scheduleRoutine(formData: FormData): Promise<never> {
  const host = formHost(formData);
  const kind = String(formData.get("kind") ?? "");
  const cadence = String(formData.get("cadence") ?? "daily");
  const enabled = formData.get("enabled") === "true";
  const tab = resolveTab(formData.get("tab"));
  const siteId = await siteIdFor(host);
  if (!siteId) redirectFresh(workspacePath(host, {error: "site_not_onboarded"}));
  try {
    await apiJson(`/v1/sites/${siteId}/routines`, {
      method: "PUT",
      body: JSON.stringify({
        kind,
        cadence,
        schedule_hour_utc: 6,
        // Monday, used only when the cadence is weekly.
        schedule_isodow: cadence === "weekly" ? 1 : null,
        schedule_dom: cadence === "monthly" ? 1 : null,
        enabled,
      }),
    });
    redirectFresh(workspacePath(host, {tab}));
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    if (error instanceof ApiError) redirectFresh(workspacePath(host, {tab, error: safeCode(error.code)}));
    throw error;
  }
}

export async function runRoutineNow(formData: FormData): Promise<never> {
  const host = formHost(formData);
  const routineId = String(formData.get("routine_id") ?? "");
  const tab = resolveTab(formData.get("tab"));
  try {
    await apiJson(`/v1/routines/${routineId}/runs`, {method: "POST"});
    redirectFresh(workspacePath(host, {tab}));
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    if (error instanceof ApiError) redirectFresh(workspacePath(host, {tab, error: safeCode(error.code)}));
    throw error;
  }
}

const BRIEF_STATUSES = new Set(["queued", "in_progress", "done", "dismissed"]);

/**
 * Move a content brief through its queue.
 *
 * A brief is advisory: this records a person's decision about a topic and
 * nothing else. It writes nothing to the site. Dismissing needs a reason,
 * which the API enforces and records in the audit trail.
 */
export async function updateBriefStatus(formData: FormData): Promise<never> {
  const briefId = String(formData.get("brief_id") ?? "");
  const status = String(formData.get("status") ?? "");
  const reason = String(formData.get("reason") ?? "").trim().slice(0, 200);
  if (!/^[0-9a-f-]{36}$/i.test(briefId) || !BRIEF_STATUSES.has(status)) {
    redirectFresh("/pilot/workspace?error=invalid_brief_update");
  }
  const back = `/pilot/workspace/briefs/${briefId}`;
  if (status === "dismissed" && !reason) redirectFresh(`${back}?error=dismiss_reason_required`);
  try {
    await apiJson(`/v1/content-briefs/${briefId}`, {
      method: "PATCH",
      body: JSON.stringify({status, reason}),
    });
  } catch (error) {
    unstable_rethrow(error);
    if (error instanceof ApiError) redirectFresh(`${back}?error=${safeCode(error.code)}`);
    throw error;
  }
  redirectFresh(`${back}?updated=${status}`);
}

const PROVIDERS = new Set(["anthropic", "openai"]);
const UUID = /^[0-9a-f-]{36}$/i;

/** Ask for an AI draft of a new-post brief. Returns to the draft's page. */
export async function requestDraft(formData: FormData): Promise<never> {
  const briefId = String(formData.get("brief_id") ?? "");
  const provider = String(formData.get("provider") ?? "");
  const authorName = String(formData.get("author_name") ?? "").trim().slice(0, 120);
  const idempotencyKey = String(formData.get("idempotency_key") ?? "");
  if (!UUID.test(briefId) || !PROVIDERS.has(provider) || idempotencyKey.length < 8) {
    redirectFresh("/pilot/workspace?error=invalid_draft_request");
  }
  const back = `/pilot/workspace/briefs/${briefId}`;
  let draftId = "";
  try {
    const created = await apiJson<{data: {id: string}}>(`/v1/content-briefs/${briefId}/drafts`, {
      method: "POST",
      body: JSON.stringify({provider, author_name: authorName, idempotency_key: idempotencyKey}),
    });
    draftId = created.data.id;
  } catch (error) {
    unstable_rethrow(error);
    if (error instanceof ApiError) redirectFresh(`${back}?error=${safeCode(error.code)}`);
    throw error;
  }
  redirectFresh(`/pilot/workspace/drafts/${draftId}`);
}

/**
 * Save a reviewer's edits and flag resolutions.
 *
 * A flag is resolved by ticking it and saying how: verified, corrected or
 * removed. The note is kept with the draft, so an approver can see what was
 * checked rather than only that something was.
 */
export async function saveDraft(formData: FormData): Promise<never> {
  const draftId = String(formData.get("draft_id") ?? "");
  const version = Number(formData.get("version") ?? 0);
  if (!UUID.test(draftId) || !Number.isInteger(version) || version < 1) {
    redirectFresh("/pilot/workspace?error=invalid_draft_update");
  }
  const back = `/pilot/workspace/drafts/${draftId}`;
  const resolved: Record<string, string> = {};
  for (const [key, value] of formData.entries()) {
    if (key.startsWith("resolve:") && value === "on") {
      const id = key.slice("resolve:".length);
      const note = String(formData.get(`note:${id}`) ?? "").trim().slice(0, 300);
      resolved[id] = note || "Checked";
    }
  }
  // Browsers submit textarea text with CRLF line endings. Stored as-is, every
  // line of an edited post differs from the model's LF original by a
  // trailing \r, and the diff reports the whole post as rewritten.
  const text = (name: string, max: number) => {
    const value = formData.get(name);
    return typeof value === "string" ? value.replace(/\r\n?/g, "\n").slice(0, max) : undefined;
  };
  try {
    await apiJson(`/v1/content-drafts/${draftId}`, {
      method: "PATCH",
      body: JSON.stringify({
        version,
        title: text("title", 200),
        slug: text("slug", 80),
        meta_description: text("meta_description", 320),
        body_markdown: text("body_markdown", 60000),
        author_name: text("author_name", 120),
        resolved_flags: resolved,
      }),
    });
  } catch (error) {
    unstable_rethrow(error);
    if (error instanceof ApiError) redirectFresh(`${back}?error=${safeCode(error.code)}`);
    throw error;
  }
  redirectFresh(`${back}?saved=1`);
}

export async function withdrawDraft(formData: FormData): Promise<never> {
  const draftId = String(formData.get("draft_id") ?? "");
  if (!UUID.test(draftId)) redirectFresh("/pilot/workspace?error=invalid_draft_update");
  const back = `/pilot/workspace/drafts/${draftId}`;
  try {
    await apiJson(`/v1/content-drafts/${draftId}/withdraw`, {method: "POST"});
  } catch (error) {
    unstable_rethrow(error);
    if (error instanceof ApiError) redirectFresh(`${back}?error=${safeCode(error.code)}`);
    throw error;
  }
  redirectFresh(`${back}?withdrawn=1`);
}

/**
 * Send a reviewed draft for approval.
 *
 * Creates a proposal to add the post as a new file. It still needs two
 * approvers who are not its author, and deploying it opens a pull request a
 * person merges -- nothing here publishes.
 */
export async function submitDraft(formData: FormData): Promise<never> {
  const draftId = String(formData.get("draft_id") ?? "");
  if (!UUID.test(draftId)) redirectFresh("/pilot/workspace?error=invalid_draft_update");
  const back = `/pilot/workspace/drafts/${draftId}`;
  try {
    await apiJson(`/v1/content-drafts/${draftId}/submit`, {method: "POST"});
  } catch (error) {
    unstable_rethrow(error);
    if (error instanceof ApiError) redirectFresh(`${back}?error=${safeCode(error.code)}`);
    throw error;
  }
  redirectFresh(`${back}?submitted=1`);
}

/**
 * Propose an llms.txt built from the last crawl.
 *
 * It becomes an ordinary new-file proposal -- two approvers who are not its
 * author, then a pull request a person merges -- so success lands on the
 * dashboard's proposal list rather than claiming anything was published.
 */
export async function proposeLlmsTxt(formData: FormData): Promise<never> {
  const host = formHost(formData);
  const back = (error: string) => workspacePath(host, {tab: "reports", error});
  let siteId: string | undefined;
  try {
    siteId = await siteIdFor(host);
    if (!siteId) redirectFresh(back("site_not_found"));
    await apiJson(`/v1/sites/${siteId}/llms-txt/proposal`, {method: "POST"});
  } catch (error) {
    unstable_rethrow(error);
    if (error instanceof ApiError) redirectFresh(back(safeCode(error.code)));
    throw error;
  }
  revalidatePath("/pilot");
  redirectFresh(pilotPath(host, {proposed: "llms_txt"}));
}
