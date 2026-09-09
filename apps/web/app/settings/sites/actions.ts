"use server";

import {cookies} from "next/headers";
import {revalidatePath} from "next/cache";
import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

import {CHALLENGE_COOKIE, type StoredChallenge} from "./challenge";

const PAGE = "/settings/sites";


type SiteEnvelope = {data: {id: string; normalized_host: string}};
type ChallengeEnvelope = {
  data: {
    id: string;
    method: string;
    record_name: string;
    record_value: string;
    token: string;
    expires_at: string;
  };
};

function redirectFresh(path: string): never {
  revalidatePath(PAGE, "page");
  redirect(path);
}

function failure(error: unknown): never {
  unstable_rethrow(error);
  const code = error instanceof ApiError ? error.code : "unexpected-error";
  redirectFresh(`${PAGE}?error=${encodeURIComponent(code)}`);
}

async function storeChallenge(challenge: StoredChallenge): Promise<void> {
  // The token is shown on the page anyway -- it has to be, it is what the
  // person types into their DNS -- so this cookie is a convenience for the
  // verify step, not a secret store. It is still httpOnly so a script on the
  // page cannot read it back.
  const value = Buffer.from(JSON.stringify(challenge)).toString("base64url");
  (await cookies()).set(CHALLENGE_COOKIE, value, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 24,
  });
}

export async function readChallenge(): Promise<StoredChallenge | null> {
  const encoded = (await cookies()).get(CHALLENGE_COOKIE)?.value;
  if (!encoded) return null;
  try {
    return JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) as StoredChallenge;
  } catch {
    return null;
  }
}

/**
 * Add a site, and immediately issue the DNS challenge that proves it is yours.
 *
 * Both in one action because a site nobody has verified can do nothing at all
 * -- it cannot be crawled, and no connector will bind to it -- so leaving
 * somebody on a list of unusable sites with no obvious next step is just a
 * worse version of the same screen.
 */
export async function addSiteAction(formData: FormData): Promise<never> {
  const name = String(formData.get("name") ?? "").trim();
  const origin = String(formData.get("canonical_origin") ?? "").trim();
  if (!name || !origin) redirectFresh(`${PAGE}?error=site_fields_required`);

  let siteId: string;
  try {
    const created = await apiJson<SiteEnvelope>("/v1/sites", {
      method: "POST",
      body: JSON.stringify({name, canonical_origin: origin, mode: "observe"}),
    });
    siteId = created.data.id;
    const challenge = await apiJson<ChallengeEnvelope>(
      `/v1/sites/${siteId}/verification-challenges`,
      {method: "POST", body: "{}"},
    );
    await storeChallenge({
      siteId,
      token: challenge.data.token,
      recordName: challenge.data.record_name,
      recordValue: challenge.data.record_value,
      expiresAt: challenge.data.expires_at,
    });
  } catch (error) {
    failure(error);
  }
  redirectFresh(`${PAGE}?site=${encodeURIComponent(siteId)}`);
}

/** Issue a fresh challenge for a site that has one expired or lost. */
export async function refreshChallengeAction(formData: FormData): Promise<never> {
  const siteId = String(formData.get("site_id") ?? "").trim();
  if (!siteId) redirectFresh(`${PAGE}?error=unexpected-error`);
  try {
    const challenge = await apiJson<ChallengeEnvelope>(
      `/v1/sites/${siteId}/verification-challenges`,
      {method: "POST", body: "{}"},
    );
    await storeChallenge({
      siteId,
      token: challenge.data.token,
      recordName: challenge.data.record_name,
      recordValue: challenge.data.record_value,
      expiresAt: challenge.data.expires_at,
    });
  } catch (error) {
    failure(error);
  }
  redirectFresh(`${PAGE}?site=${encodeURIComponent(siteId)}`);
}

/**
 * Ask the resolver whether the record is there yet.
 *
 * A failure here is very often "not propagated yet" rather than "wrong", which
 * is why the page says so and the challenge is not discarded on a miss.
 */
export async function verifySiteAction(formData: FormData): Promise<never> {
  const siteId = String(formData.get("site_id") ?? "").trim();
  const stored = await readChallenge();
  if (!stored || stored.siteId !== siteId) {
    redirectFresh(`${PAGE}?error=verification_challenge_missing&site=${encodeURIComponent(siteId)}`);
  }
  try {
    await apiJson(`/v1/sites/${siteId}/verify`, {
      method: "POST",
      body: JSON.stringify({token: stored.token}),
    });
    (await cookies()).delete(CHALLENGE_COOKIE);
  } catch (error) {
    unstable_rethrow(error);
    const code = error instanceof ApiError ? error.code : "unexpected-error";
    redirectFresh(`${PAGE}?error=${encodeURIComponent(code)}&site=${encodeURIComponent(siteId)}`);
  }
  redirectFresh(`${PAGE}?verified=${encodeURIComponent(siteId)}`);
}

/**
 * Let a Cloudflare-hosted zone publish the record for you.
 *
 * Optional, and deliberately presented as a shortcut rather than the path:
 * verification is a plain TXT record and works identically at any registrar.
 * Requiring an API token from one particular DNS provider would exclude
 * everybody who is not with them.
 */
export async function publishViaCloudflareAction(formData: FormData): Promise<never> {
  const siteId = String(formData.get("site_id") ?? "").trim();
  const zoneId = String(formData.get("zone_id") ?? "").trim();
  const apiToken = String(formData.get("api_token") ?? "").trim();
  const stored = await readChallenge();
  if (!siteId || !zoneId || !apiToken) {
    redirectFresh(`${PAGE}?error=cloudflare_fields_required&site=${encodeURIComponent(siteId)}`);
  }
  if (!stored || stored.siteId !== siteId) {
    redirectFresh(`${PAGE}?error=verification_challenge_missing&site=${encodeURIComponent(siteId)}`);
  }
  try {
    await apiJson(`/v1/sites/${siteId}/dns-connectors/cloudflare`, {
      method: "POST",
      body: JSON.stringify({zone_id: zoneId, api_token: apiToken}),
    });
    await apiJson(`/v1/sites/${siteId}/dns-connectors/cloudflare/verification`, {
      method: "POST",
      body: JSON.stringify({token: stored.token}),
    });
  } catch (error) {
    unstable_rethrow(error);
    const code = error instanceof ApiError ? error.code : "unexpected-error";
    redirectFresh(`${PAGE}?error=${encodeURIComponent(code)}&site=${encodeURIComponent(siteId)}`);
  }
  redirectFresh(`${PAGE}?published=${encodeURIComponent(siteId)}`);
}
