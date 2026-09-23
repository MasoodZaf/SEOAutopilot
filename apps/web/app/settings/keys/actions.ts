"use server";

import {revalidatePath} from "next/cache";
import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

const PAGE = "/settings/keys";

function redirectFresh(path: string): never {
  revalidatePath(PAGE, "page");
  redirect(path);
}

function failure(error: unknown): never {
  unstable_rethrow(error);
  const code = error instanceof ApiError ? error.code : "unexpected-error";
  redirectFresh(`${PAGE}?error=${encodeURIComponent(code)}`);
}

/**
 * Store this workspace's own Google OAuth client.
 *
 * The values come from the tenant's own Google Cloud project, which is the
 * point: their consent screen then names their application, the API quota
 * spent is theirs, and revoking it affects nobody else.
 */
export async function saveGoogleClientAction(formData: FormData): Promise<never> {
  const clientId = String(formData.get("client_id") ?? "").trim();
  const clientSecret = String(formData.get("client_secret") ?? "").trim();
  if (!clientId || !clientSecret) redirectFresh(`${PAGE}?error=google_client_incomplete`);

  try {
    await apiJson("/v1/tenant/credentials/google_oauth_client", {
      method: "PUT",
      body: JSON.stringify({client_id: clientId, client_secret: clientSecret}),
    });
  } catch (error) {
    failure(error);
  }
  redirectFresh(`${PAGE}?saved=google`);
}

/**
 * Store this workspace's own GitHub App.
 *
 * The private key is pasted whole, PEM header and footer included. It is
 * parsed on the way in rather than at the first installation, because finding
 * out it was malformed *after* granting repository access is a worse place to
 * discover it.
 */
export async function saveGithubAppAction(formData: FormData): Promise<never> {
  const appId = String(formData.get("app_id") ?? "").trim();
  const appSlug = String(formData.get("app_slug") ?? "").trim();
  const privateKey = String(formData.get("private_key") ?? "").trim();
  const clientId = String(formData.get("client_id") ?? "").trim();
  const clientSecret = String(formData.get("client_secret") ?? "").trim();
  if (!appId || !appSlug || !privateKey) redirectFresh(`${PAGE}?error=github_app_incomplete`);

  try {
    await apiJson("/v1/tenant/credentials/github_app", {
      method: "PUT",
      body: JSON.stringify({
        app_id: appId,
        app_slug: appSlug,
        private_key: privateKey,
        client_id: clientId,
        client_secret: clientSecret,
      }),
    });
  } catch (error) {
    failure(error);
  }
  redirectFresh(`${PAGE}?saved=github`);
}

export async function revokeCredentialAction(formData: FormData): Promise<never> {
  const provider = String(formData.get("provider") ?? "").trim();
  if (!provider) redirectFresh(`${PAGE}?error=unexpected-error`);
  try {
    await apiJson(`/v1/tenant/credentials/${encodeURIComponent(provider)}`, {method: "DELETE"});
  } catch (error) {
    failure(error);
  }
  redirectFresh(`${PAGE}?revoked=${encodeURIComponent(provider)}`);
}

const AI_PROVIDERS = new Set(["anthropic_api_key", "openai_api_key"]);

/**
 * Store a key this workspace's AI blog drafts are written with.
 *
 * Bring-your-own-key: drafting never falls back to a deployment key, so a
 * workspace without one simply cannot request drafts from that provider.
 */
export async function saveAiKeyAction(formData: FormData): Promise<never> {
  const provider = String(formData.get("provider") ?? "");
  const apiKey = String(formData.get("api_key") ?? "").trim();
  if (!AI_PROVIDERS.has(provider)) redirectFresh(`${PAGE}?error=unexpected-error`);
  if (!apiKey) redirectFresh(`${PAGE}?error=${provider}_invalid`);
  try {
    await apiJson(`/v1/tenant/credentials/${provider}`, {
      method: "PUT",
      body: JSON.stringify({api_key: apiKey}),
    });
  } catch (error) {
    failure(error);
  }
  redirectFresh(`${PAGE}?saved=${provider}`);
}
