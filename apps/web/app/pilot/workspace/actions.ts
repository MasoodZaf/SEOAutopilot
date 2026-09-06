"use server";

import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

import {resolvePortfolioSite} from "../portfolio.mjs";
import type {AgentSession} from "./model";
import {resolveTab, workspacePath} from "./paths";

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
  return resolvePortfolioSite(formData.get("site_host")).host;
}

export async function startConversation(formData: FormData): Promise<never> {
  const host = formHost(formData);
  const rawTitle = formData.get("title");
  const title = typeof rawTitle === "string" && rawTitle.trim() ? rawTitle.trim() : "New conversation";
  const siteId = await siteIdFor(host);
  if (!siteId) redirect(workspacePath(host, {error: "site_not_onboarded"}));
  try {
    const created = await apiJson<SessionEnvelope>(`/v1/sites/${siteId}/agent-sessions`, {
      method: "POST",
      body: JSON.stringify({title: title.slice(0, 200)}),
    });
    redirect(workspacePath(host, {tab: "chat", session: created.data.id}));
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    if (error instanceof ApiError) redirect(workspacePath(host, {error: safeCode(error.code)}));
    throw error;
  }
}

export async function sendMessage(formData: FormData): Promise<never> {
  const host = formHost(formData);
  const sessionId = String(formData.get("session_id") ?? "");
  const body = String(formData.get("body") ?? "").trim();
  const skillKey = formData.get("skill_key");
  if (!sessionId || !body) redirect(workspacePath(host, {tab: "chat", session: sessionId}));
  try {
    await apiJson(`/v1/agent-sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        body: body.slice(0, 8000),
        skill_key: typeof skillKey === "string" && skillKey ? skillKey : null,
      }),
    });
    redirect(workspacePath(host, {tab: "chat", session: sessionId}));
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    if (error instanceof ApiError) {
      redirect(workspacePath(host, {tab: "chat", session: sessionId, error: safeCode(error.code)}));
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
  if (!siteId) redirect(workspacePath(host, {error: "site_not_onboarded"}));
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
    redirect(workspacePath(host, {tab}));
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    if (error instanceof ApiError) redirect(workspacePath(host, {tab, error: safeCode(error.code)}));
    throw error;
  }
}

export async function runRoutineNow(formData: FormData): Promise<never> {
  const host = formHost(formData);
  const routineId = String(formData.get("routine_id") ?? "");
  const tab = resolveTab(formData.get("tab"));
  try {
    await apiJson(`/v1/routines/${routineId}/runs`, {method: "POST"});
    redirect(workspacePath(host, {tab}));
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    if (error instanceof ApiError) redirect(workspacePath(host, {tab, error: safeCode(error.code)}));
    throw error;
  }
}
