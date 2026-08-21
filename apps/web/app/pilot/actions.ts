"use server";

import {cookies} from "next/headers";
import {redirect} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

import {challengeCookie, type Site} from "./model";

type SiteCollection = {data: Site[]};
type SiteEnvelope = {data: Site};
type ChallengeEnvelope = {
  data: {
    record_name: string;
    record_value: string;
    token: string;
    expires_at: string;
  };
};
type ConnectorAuthorizationEnvelope = {data: {authorization_url: string}};
type CrawlEnvelope = {data: {id: string; status: string}};

function errorUrl(code: string): string {
  const safeCode = /^[a-z0-9_-]+$/i.test(code) ? code : "unexpected-error";
  return `/pilot?error=${encodeURIComponent(safeCode)}`;
}

async function codearcSite(): Promise<Site | undefined> {
  const collection = await apiJson<SiteCollection>("/v1/sites");
  return collection.data.find((site) => site.normalized_host === "codearc.net");
}

async function storeChallenge(siteId: string, challenge: ChallengeEnvelope["data"]) {
  const value = Buffer.from(JSON.stringify({siteId, ...challenge})).toString("base64url");
  (await cookies()).set(challengeCookie, value, {
    httpOnly: true,
    sameSite: "strict",
    secure: false,
    path: "/",
    maxAge: 30 * 60,
  });
}

export async function onboardCodearc(): Promise<never> {
  try {
    await apiJson("/v1/local-pilot/bootstrap", {method: "POST"});
    let site = await codearcSite();
    if (!site) {
      site = (
        await apiJson<SiteEnvelope>("/v1/sites", {
          method: "POST",
          body: JSON.stringify({
            name: "CodeArc",
            canonical_origin: "https://codearc.net",
            mode: "observe",
          }),
        })
      ).data;
    }
    if (site.status !== "active") {
      const challenge = await apiJson<ChallengeEnvelope>(
        `/v1/sites/${site.id}/verification-challenges`,
        {method: "POST", body: "{}"},
      );
      await storeChallenge(site.id, challenge.data);
    }
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect("/pilot");
}

export async function refreshDnsChallenge(): Promise<never> {
  try {
    const site = await codearcSite();
    if (!site) redirect("/pilot");
    const challenge = await apiJson<ChallengeEnvelope>(
      `/v1/sites/${site.id}/verification-challenges`,
      {method: "POST", body: "{}"},
    );
    await storeChallenge(site.id, challenge.data);
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect("/pilot");
}

export async function verifyCodearcDns(): Promise<never> {
  try {
    const encoded = (await cookies()).get(challengeCookie)?.value;
    if (!encoded) redirect(errorUrl("verification-challenge-expired"));
    const challenge = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) as {
      siteId: string;
      token: string;
    };
    await apiJson(`/v1/sites/${challenge.siteId}/verify`, {
      method: "POST",
      body: JSON.stringify({token: challenge.token}),
    });
    (await cookies()).delete(challengeCookie);
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect("/pilot?verified=true");
}

export async function connectSearchConsole(): Promise<never> {
  let authorizationUrl: string;
  try {
    const site = await codearcSite();
    if (!site) redirect("/pilot");
    const result = await apiJson<ConnectorAuthorizationEnvelope>(
      `/v1/sites/${site.id}/connectors/google_search_console/authorize`,
      {
        method: "POST",
        body: JSON.stringify({property_ref: "https://codearc.net/"}),
      },
    );
    authorizationUrl = result.data.authorization_url;
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(authorizationUrl);
}

export async function startFirstCrawl(): Promise<never> {
  try {
    const site = await codearcSite();
    if (!site) redirect("/pilot");
    const result = await apiJson<CrawlEnvelope>(`/v1/sites/${site.id}/crawls`, {
      method: "POST",
      body: JSON.stringify({
        kind: "full",
        max_pages: 500,
        max_depth: 10,
        render_policy: "auto",
      }),
    });
    (await cookies()).set("seo-autopilot-last-crawl", result.data.id, {
      httpOnly: true,
      sameSite: "strict",
      secure: false,
      path: "/",
      maxAge: 24 * 60 * 60,
    });
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect("/pilot?crawl=queued");
}

export async function startPerformanceRun(formData: FormData): Promise<never> {
  try {
    const site = await codearcSite();
    if (!site) redirect("/pilot");
    const idempotencyKey = String(formData.get("idempotency_key") ?? "");
    await apiJson(`/v1/sites/${site.id}/performance-runs`, {
      method: "POST",
      headers: {"Idempotency-Key": idempotencyKey},
      body: "{}",
    });
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect("/pilot?performance=queued");
}

export async function createCalibrationSet(formData: FormData): Promise<never> {
  try {
    const site = await codearcSite();
    if (!site) redirect("/pilot");
    const idempotencyKey = String(formData.get("idempotency_key") ?? "");
    await apiJson(`/v1/sites/${site.id}/calibrations`, {
      method: "POST",
      headers: {"Idempotency-Key": idempotencyKey},
      body: JSON.stringify({target_size: 20}),
    });
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect("/pilot?calibration=created");
}

export async function submitCalibrationReview(formData: FormData): Promise<never> {
  const itemId = String(formData.get("item_id") ?? "");
  const safeItemId = /^[0-9a-f-]{36}$/i.test(itemId) ? itemId : "";
  if (!safeItemId) redirect(errorUrl("invalid-calibration-item"));
  try {
    const idempotencyKey = String(formData.get("idempotency_key") ?? "");
    await apiJson(`/v1/calibration-items/${safeItemId}/reviews`, {
      method: "POST",
      headers: {"Idempotency-Key": idempotencyKey},
      body: JSON.stringify({
        accuracy_label: String(formData.get("accuracy_label") ?? ""),
        actionability: String(formData.get("actionability") ?? ""),
        severity_fit: String(formData.get("severity_fit") ?? ""),
        notes: String(formData.get("notes") ?? ""),
      }),
    });
  } catch (error) {
    const code = error instanceof ApiError ? error.code : "unexpected-error";
    redirect(`/pilot/review/${safeItemId}?error=${encodeURIComponent(code)}`);
  }
  redirect("/pilot?reviewed=true");
}

export async function toggleEmergencyFreezeAction(formData: FormData): Promise<never> {
  try {
    const site = await codearcSite();
    if (!site) redirect("/pilot");
    const currentFreeze = formData.get("current_freeze") === "true";
    if (currentFreeze) {
      await apiJson(`/v1/sites/${site.id}/governance/unfreeze`, {method: "POST"});
    } else {
      await apiJson(`/v1/sites/${site.id}/governance/freeze`, {
        method: "POST",
        body: JSON.stringify({notes: "Manual emergency freeze from web dashboard"}),
      });
    }
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect("/pilot?governance=updated");
}

export async function runPolicySimulationAction(): Promise<never> {
  try {
    const site = await codearcSite();
    if (!site) redirect("/pilot");
    await apiJson(`/v1/sites/${site.id}/simulation`, {method: "POST"});
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect("/pilot?simulation=completed");
}

export async function approveProposalAction(formData: FormData): Promise<never> {
  const proposalId = String(formData.get("proposal_id") ?? "");
  try {
    await apiJson(`/v1/proposals/${proposalId}/approvals`, {
      method: "POST",
      body: JSON.stringify({decision: "approved", notes: "Approved from web dashboard"}),
    });
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect("/pilot?proposal=approved");
}

export async function deployProposalAction(formData: FormData): Promise<never> {
  const proposalId = String(formData.get("proposal_id") ?? "");
  const idempotencyKey = String(formData.get("idempotency_key") ?? "");
  try {
    await apiJson(`/v1/proposals/${proposalId}/deploy`, {
      method: "POST",
      headers: {"Idempotency-Key": idempotencyKey},
      body: JSON.stringify({connector_type: "github"}),
    });
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect("/pilot?proposal=deployed");
}

export async function rollbackProposalAction(formData: FormData): Promise<never> {
  const proposalId = String(formData.get("proposal_id") ?? "");
  try {
    await apiJson(`/v1/proposals/${proposalId}/rollback`, {
      method: "POST",
      body: JSON.stringify({notes: "Rollback triggered from web dashboard"}),
    });
  } catch (error) {
    redirect(errorUrl(error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect("/pilot?proposal=rolled_back");
}

