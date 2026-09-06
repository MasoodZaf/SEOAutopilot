"use server";

import {cookies} from "next/headers";
import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

import {challengeCookie, type Site} from "./model";
import {pilotPath, resolvePortfolioSite} from "./portfolio.mjs";

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

function errorUrl(host: string, code: string): string {
  const safeCode = /^[a-z0-9_-]+$/i.test(code) ? code : "unexpected-error";
  return pilotPath(host, {error: safeCode});
}

function actionHost(formData: FormData): string {
  return resolvePortfolioSite(formData.get("site_host")).host;
}

async function portfolioSite(host: string): Promise<Site | undefined> {
  const collection = await apiJson<SiteCollection>("/v1/sites");
  return collection.data.find((site) => site.normalized_host === host);
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

export async function onboardPortfolioSite(formData: FormData): Promise<never> {
  const target = resolvePortfolioSite(formData.get("site_host"));
  try {
    await apiJson("/v1/local-pilot/bootstrap", {method: "POST"});
    let site = await portfolioSite(target.host);
    if (!site) {
      site = (
        await apiJson<SiteEnvelope>("/v1/sites", {
          method: "POST",
          body: JSON.stringify({
            name: target.name,
            canonical_origin: target.origin,
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
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(target.host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(target.host));
}

export async function refreshDnsChallenge(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await portfolioSite(host);
    if (!site) redirect(pilotPath(host));
    const challenge = await apiJson<ChallengeEnvelope>(
      `/v1/sites/${site.id}/verification-challenges`,
      {method: "POST", body: "{}"},
    );
    await storeChallenge(site.id, challenge.data);
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host));
}

export async function verifyPortfolioDns(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const encoded = (await cookies()).get(challengeCookie)?.value;
    if (!encoded) redirect(errorUrl(host, "verification-challenge-expired"));
    const challenge = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) as {
      siteId: string;
      token: string;
    };
    const site = await portfolioSite(host);
    if (!site || site.id !== challenge.siteId) redirect(errorUrl(host, "verification-challenge-mismatch"));
    await apiJson(`/v1/sites/${site.id}/verify`, {
      method: "POST",
      body: JSON.stringify({token: challenge.token}),
    });
    (await cookies()).delete(challengeCookie);
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host, {verified: "true"}));
}

export async function connectSearchConsole(formData: FormData): Promise<never> {
  const target = resolvePortfolioSite(formData.get("site_host"));
  let authorizationUrl: string;
  try {
    const site = await portfolioSite(target.host);
    if (!site) redirect(pilotPath(target.host));
    const result = await apiJson<ConnectorAuthorizationEnvelope>(
      `/v1/sites/${site.id}/connectors/google_search_console/authorize`,
      {
        method: "POST",
        body: JSON.stringify({property_ref: `${target.origin}/`}),
      },
    );
    authorizationUrl = result.data.authorization_url;
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(target.host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(authorizationUrl);
}

export async function connectDnsProvider(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  const providerKey = String(formData.get("provider_key") ?? "").trim();
  const zoneId = String(formData.get("zone_id") ?? "").trim();
  const apiToken = String(formData.get("api_token") ?? "").trim();
  if (!/^[a-z0-9_-]{2,48}$/i.test(providerKey) || !/^[a-f0-9]{32}$/i.test(zoneId) || apiToken.length < 20) {
    redirect(errorUrl(host, "dns-provider-connection-invalid"));
  }
  try {
    const site = await portfolioSite(host);
    if (!site) redirect(pilotPath(host));
    await apiJson(`/v1/sites/${site.id}/dns-connectors/${encodeURIComponent(providerKey)}`, {
      method: "POST",
      body: JSON.stringify({zone_id: zoneId.toLowerCase(), api_token: apiToken}),
    });
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host, {dns_provider: "connected"}));
}

export async function createDnsProviderVerification(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  const providerKey = String(formData.get("provider_key") ?? "").trim();
  try {
    const encoded = (await cookies()).get(challengeCookie)?.value;
    if (!encoded) redirect(errorUrl(host, "verification-challenge-expired"));
    const challenge = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) as {siteId: string; token: string};
    const site = await portfolioSite(host);
    if (!site || site.id !== challenge.siteId) redirect(errorUrl(host, "verification-challenge-mismatch"));
    await apiJson(`/v1/sites/${site.id}/dns-connectors/${encodeURIComponent(providerKey)}/verification`, {
      method: "POST",
      body: JSON.stringify({token: challenge.token}),
    });
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host, {dns_provider: "record-created"}));
}

export async function startFirstCrawl(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await portfolioSite(host);
    if (!site) redirect(pilotPath(host));
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
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host, {crawl: "queued"}));
}

export async function startPerformanceRun(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await portfolioSite(host);
    if (!site) redirect(pilotPath(host));
    const idempotencyKey = String(formData.get("idempotency_key") ?? "");
    await apiJson(`/v1/sites/${site.id}/performance-runs`, {
      method: "POST",
      headers: {"Idempotency-Key": idempotencyKey},
      body: "{}",
    });
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host, {performance: "queued"}));
}

export async function createCalibrationSet(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await portfolioSite(host);
    if (!site) redirect(pilotPath(host));
    const idempotencyKey = String(formData.get("idempotency_key") ?? "");
    await apiJson(`/v1/sites/${site.id}/calibrations`, {
      method: "POST",
      headers: {"Idempotency-Key": idempotencyKey},
      body: JSON.stringify({target_size: 20}),
    });
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host, {calibration: "created"}));
}

export async function submitCalibrationReview(formData: FormData): Promise<never> {
  const itemId = String(formData.get("item_id") ?? "");
  const safeItemId = /^[0-9a-f-]{36}$/i.test(itemId) ? itemId : "";
  if (!safeItemId) redirect(errorUrl("codearc.net", "invalid-calibration-item"));
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
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    const code = error instanceof ApiError ? error.code : "unexpected-error";
    redirect(`/pilot/review/${safeItemId}?error=${encodeURIComponent(code)}`);
  }
  redirect("/pilot?reviewed=true");
}

export async function toggleEmergencyFreezeAction(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await portfolioSite(host);
    if (!site) redirect(pilotPath(host));
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
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host, {governance: "updated"}));
}

export async function runPolicySimulationAction(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await portfolioSite(host);
    if (!site) redirect(pilotPath(host));
    await apiJson(`/v1/sites/${site.id}/simulation`, {method: "POST"});
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host, {simulation: "completed"}));
}

export async function approveProposalAction(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  const proposalId = String(formData.get("proposal_id") ?? "");
  try {
    await apiJson(`/v1/proposals/${proposalId}/approvals`, {
      method: "POST",
      body: JSON.stringify({decision: "approved", notes: "Approved from web dashboard"}),
    });
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host, {proposal: "approved"}));
}

export async function deployProposalAction(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  const proposalId = String(formData.get("proposal_id") ?? "");
  const idempotencyKey = String(formData.get("idempotency_key") ?? "");
  try {
    await apiJson(`/v1/proposals/${proposalId}/deploy`, {
      method: "POST",
      headers: {"Idempotency-Key": idempotencyKey},
      body: JSON.stringify({connector_type: "github"}),
    });
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host, {proposal: "deployed"}));
}

export async function rollbackProposalAction(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  const proposalId = String(formData.get("proposal_id") ?? "");
  try {
    await apiJson(`/v1/proposals/${proposalId}/rollback`, {
      method: "POST",
      body: JSON.stringify({notes: "Rollback triggered from web dashboard"}),
    });
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirect(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirect(pilotPath(host, {proposal: "rolled_back"}));
}
