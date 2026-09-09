"use server";

import {cookies} from "next/headers";
import {revalidatePath} from "next/cache";
import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

import {challengeCookie, type Site} from "./model";
import {pilotPath, safeHost} from "./site-selection.mjs";

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

/**
 * Redirect, and make sure the page landed on is actually re-rendered.
 *
 * Every action here mutates through the API and then redirects back to
 * /pilot. Without invalidating the route first, that redirect is served from
 * the client router cache -- and when the target is the URL already on screen
 * it is a no-op navigation, so nothing re-renders and nothing appears to have
 * happened at all.
 *
 * That is not theoretical. On 2026-09-08 it made the dashboard behave as if
 * every button but the first were dead: withdraw a proposal, and the next
 * click did nothing, silently, until the page was reloaded by hand. Nineteen
 * withdrawals took nineteen manual reloads. A 409 refused by the daily change
 * budget looked identical to a click that never registered, which is the worse
 * half -- a real refusal the operator could not see.
 *
 * `redirect()` throws, so this never returns and the revalidation has to come
 * first.
 */
function redirectFresh(path: string): never {
  revalidatePath("/pilot", "page");
  redirect(path);
}

function errorUrl(host: string, code: string): string {
  const safeCode = /^[a-z0-9_-]+$/i.test(code) ? code : "unexpected-error";
  return pilotPath(host, {error: safeCode});
}

function actionHost(formData: FormData): string {
  return safeHost(formData.get("site_host"));
}

/**
 * The workspace's own site with this host, if it has one.
 *
 * The list comes from `/v1/sites`, which is tenant scoped, so a host belonging
 * to another workspace simply is not in it. That -- not a hardcoded list of
 * three of our domains -- is what stops one tenant acting on another's site.
 */
async function ownedSite(host: string): Promise<Site | undefined> {
  if (!host) return undefined;
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

/*
 * `onboardPortfolioSite` lived here and has been removed.
 *
 * It created a site from a hardcoded name and origin, which is meaningless for
 * a workspace that is not ours, and its first call was
 * `POST /v1/local-pilot/bootstrap` -- a route that answers 404 whenever
 * `app_env` is not `development`. So on the production host the button could
 * not work at all: the 404 threw before a site was ever created, and every
 * onboarding attempt redirected to a generic error. Adding a site now lives at
 * /settings/sites, where the name and the address are the operator's to give.
 */

export async function refreshDnsChallenge(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await ownedSite(host);
    if (!site) redirectFresh(pilotPath(host));
    const challenge = await apiJson<ChallengeEnvelope>(
      `/v1/sites/${site.id}/verification-challenges`,
      {method: "POST", body: "{}"},
    );
    await storeChallenge(site.id, challenge.data);
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host));
}

export async function verifyPortfolioDns(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const encoded = (await cookies()).get(challengeCookie)?.value;
    if (!encoded) redirectFresh(errorUrl(host, "verification-challenge-expired"));
    const challenge = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) as {
      siteId: string;
      token: string;
    };
    const site = await ownedSite(host);
    if (!site || site.id !== challenge.siteId) redirectFresh(errorUrl(host, "verification-challenge-mismatch"));
    await apiJson(`/v1/sites/${site.id}/verify`, {
      method: "POST",
      body: JSON.stringify({token: challenge.token}),
    });
    (await cookies()).delete(challengeCookie);
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {verified: "true"}));
}

export async function connectSearchConsole(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  let authorizationUrl: string;
  try {
    const site = await ownedSite(host);
    if (!site) redirectFresh(pilotPath(host));
    const result = await apiJson<ConnectorAuthorizationEnvelope>(
      `/v1/sites/${site.id}/connectors/google_search_console/authorize`,
      {
        method: "POST",
        body: JSON.stringify({property_ref: `${site.canonical_origin}/`}),
      },
    );
    authorizationUrl = result.data.authorization_url;
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(authorizationUrl);
}

export async function connectDnsProvider(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  const providerKey = String(formData.get("provider_key") ?? "").trim();
  const zoneId = String(formData.get("zone_id") ?? "").trim();
  const apiToken = String(formData.get("api_token") ?? "").trim();
  if (!/^[a-z0-9_-]{2,48}$/i.test(providerKey) || !/^[a-f0-9]{32}$/i.test(zoneId) || apiToken.length < 20) {
    redirectFresh(errorUrl(host, "dns-provider-connection-invalid"));
  }
  try {
    const site = await ownedSite(host);
    if (!site) redirectFresh(pilotPath(host));
    await apiJson(`/v1/sites/${site.id}/dns-connectors/${encodeURIComponent(providerKey)}`, {
      method: "POST",
      body: JSON.stringify({zone_id: zoneId.toLowerCase(), api_token: apiToken}),
    });
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {dns_provider: "connected"}));
}

export async function createDnsProviderVerification(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  const providerKey = String(formData.get("provider_key") ?? "").trim();
  try {
    const encoded = (await cookies()).get(challengeCookie)?.value;
    if (!encoded) redirectFresh(errorUrl(host, "verification-challenge-expired"));
    const challenge = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) as {siteId: string; token: string};
    const site = await ownedSite(host);
    if (!site || site.id !== challenge.siteId) redirectFresh(errorUrl(host, "verification-challenge-mismatch"));
    await apiJson(`/v1/sites/${site.id}/dns-connectors/${encodeURIComponent(providerKey)}/verification`, {
      method: "POST",
      body: JSON.stringify({token: challenge.token}),
    });
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {dns_provider: "record-created"}));
}

export async function startFirstCrawl(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await ownedSite(host);
    if (!site) redirectFresh(pilotPath(host));
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
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {crawl: "queued"}));
}

export async function startPerformanceRun(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await ownedSite(host);
    if (!site) redirectFresh(pilotPath(host));
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
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {performance: "queued"}));
}

export async function createCalibrationSet(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await ownedSite(host);
    if (!site) redirectFresh(pilotPath(host));
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
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {calibration: "created"}));
}

export async function submitCalibrationReview(formData: FormData): Promise<never> {
  const itemId = String(formData.get("item_id") ?? "");
  const safeItemId = /^[0-9a-f-]{36}$/i.test(itemId) ? itemId : "";
  if (!safeItemId) redirectFresh(errorUrl("codearc.net", "invalid-calibration-item"));
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
    redirectFresh(`/pilot/review/${safeItemId}?error=${encodeURIComponent(code)}`);
  }
  redirectFresh("/pilot?reviewed=true");
}

export async function toggleEmergencyFreezeAction(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await ownedSite(host);
    if (!site) redirectFresh(pilotPath(host));
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
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {governance: "updated"}));
}

export async function runPolicySimulationAction(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  try {
    const site = await ownedSite(host);
    if (!site) redirectFresh(pilotPath(host));
    await apiJson(`/v1/sites/${site.id}/simulation`, {method: "POST"});
  } catch (error) {
    // `redirect()` throws; let its control-flow signal through so this
    // action's own redirects are not rewritten as a generic error.
    unstable_rethrow(error);
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {simulation: "completed"}));
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
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {proposal: "approved"}));
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
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {proposal: "deployed"}));
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
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {proposal: "rolled_back"}));
}

/**
 * Turn an opportunity into a proposal.
 *
 * The step the workspace never had. Opportunities were listed, proposals could
 * be approved, deployed and rolled back, and nothing in the app connected the
 * two -- so a finding became a change only if an operator holding the pilot
 * token called the endpoint by hand. Retiring that token on 2026-09-08 left
 * wordkitapp.com with 13 scored opportunities, a repository to write to, and no
 * route between them.
 *
 * Drafting is not approving. The draft goes through `create_proposal` like any
 * hand-authored change: classified by risk, validated, and left waiting for a
 * human. This button writes nothing to the site.
 */
export async function draftProposalAction(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  const opportunityId = String(formData.get("opportunity_id") ?? "");
  try {
    await apiJson(`/v1/opportunities/${opportunityId}/proposal-draft`, {method: "POST"});
  } catch (error) {
    unstable_rethrow(error);
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {proposal: "drafted"}));
}

/**
 * Move a site between observe, recommend and autopilot.
 *
 * Readable in this dashboard since it was built and changeable only by an
 * operator holding the pilot token, which means since this morning it has been
 * changeable by nobody. A site could be onboarded, verified, crawled, analysed
 * and drafted against, and never leave `observe` — where every deployment is
 * refused with `site_mode_blocks_deployment`.
 *
 * The API treats this as a governance decision rather than a setting: owner or
 * admin only, a stated reason, an audit event and an outbox event, the same as
 * a freeze. Climbing is constrained — a site must be verified and unfrozen —
 * and descending never is, because the way to stop a site being changed must
 * not itself be blockable.
 */
export async function setSiteModeAction(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  const siteId = String(formData.get("site_id") ?? "");
  const mode = String(formData.get("mode") ?? "");
  const reason = String(formData.get("reason") ?? "").trim();
  if (!reason) redirectFresh(errorUrl(host, "a_reason_is_required"));
  try {
    await apiJson(`/v1/sites/${siteId}/governance/mode`, {
      method: "PATCH",
      body: JSON.stringify({mode, reason}),
    });
  } catch (error) {
    unstable_rethrow(error);
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {governance: `mode-${mode}`}));
}

/**
 * How many approvers this site requires, over the risk tier's own default.
 *
 * Raising is always allowed. Lowering stops at the tier floor for anything
 * touching canonical, robots or redirect directives — those are forced to high
 * risk precisely so no per-site setting can reach them — so this cannot talk a
 * dangerous change down to one pair of eyes.
 *
 * Note it is read from the proposal, not the site, at approval time: the count
 * is frozen into each proposal when it is drafted. Changing it here governs
 * proposals drafted afterwards, and leaves existing ones as they were.
 */
export async function setApproverCountAction(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  const siteId = String(formData.get("site_id") ?? "");
  const raw = String(formData.get("required_approver_count") ?? "").trim();
  const clear = raw === "";
  try {
    await apiJson(`/v1/sites/${siteId}/governance`, {
      method: "PATCH",
      body: JSON.stringify(
        clear
          ? {clear_required_approver_count: true}
          : {required_approver_count: Number(raw)},
      ),
    });
  } catch (error) {
    unstable_rethrow(error);
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {governance: clear ? "approvers-default" : `approvers-${raw}`}));
}

/**
 * Withdraw a proposal you wrote.
 *
 * An author may not approve their own change, and until now could not clear it
 * either -- there was no reject or withdraw anywhere in this dashboard. A draft
 * that turned out to be wrong stayed in the queue for ever, and the only way
 * past it was an operator with the pilot token.
 *
 * The API has always allowed this and says why: withdrawing is a different act
 * from approving. It ends the proposal, can never put a change on a live site,
 * and is the only way for whoever wrote a bad draft to clear it without
 * spending a reviewer on a change nobody wants.
 */
export async function withdrawProposalAction(formData: FormData): Promise<never> {
  const host = actionHost(formData);
  const proposalId = String(formData.get("proposal_id") ?? "");
  try {
    await apiJson(`/v1/proposals/${proposalId}/approvals`, {
      method: "POST",
      body: JSON.stringify({decision: "rejected", notes: "Withdrawn from web dashboard"}),
    });
  } catch (error) {
    unstable_rethrow(error);
    redirectFresh(errorUrl(host, error instanceof ApiError ? error.code : "unexpected-error"));
  }
  redirectFresh(pilotPath(host, {proposal: "withdrawn"}));
}
