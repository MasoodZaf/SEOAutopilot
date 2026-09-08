"use server";

import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

const PAGE = "/settings/connectors";

type AuthorizationEnvelope = {data: {authorization_url: string}};

function failure(error: unknown): never {
  // `redirect()` throws; let its control-flow signal through so this action's
  // own redirects are not rewritten as a generic error.
  unstable_rethrow(error);
  const code = error instanceof ApiError ? error.code : "unexpected-error";
  redirect(`${PAGE}?error=${encodeURIComponent(code)}`);
}

/**
 * Begin the GA4 consent flow for one site.
 *
 * The property is bound to the site at the *callback*, not here: a GA4 property
 * reference names nothing on its own, so the service checks that one of the
 * property's own data streams collects from the host this tenant has already
 * verified. Anything typed into this form is a request, not a claim.
 */
export async function connectAnalyticsAction(formData: FormData): Promise<never> {
  const siteId = String(formData.get("site_id") ?? "").trim();
  const propertyRef = String(formData.get("property_ref") ?? "").trim();
  if (!siteId || !propertyRef) redirect(`${PAGE}?error=site_and_property_required`);

  let authorizationUrl: string;
  try {
    const result = await apiJson<AuthorizationEnvelope>(
      `/v1/sites/${siteId}/connectors/google_analytics/authorize`,
      {method: "POST", body: JSON.stringify({property_ref: propertyRef})},
    );
    authorizationUrl = result.data.authorization_url;
  } catch (error) {
    failure(error);
  }
  redirect(authorizationUrl);
}

/**
 * Begin the Search Console consent flow for one site.
 *
 * This existed as an API endpoint with nothing calling it. The page reported a
 * Search Console status and offered no way to change it, so a connector left in
 * `pending_authorization` could only be finished by an operator holding the
 * pilot token. Retiring that token on 2026-09-08 turned a gap into a dead end:
 * codearc.net had an abandoned authorization and no route to complete it.
 *
 * Unlike GA4, a Search Console property names its own site, so the binding is a
 * comparison the service can make directly against the verified host. The two
 * accepted shapes are the two Google offers, and they are not interchangeable:
 * a domain property covers every subdomain and protocol, a URL-prefix property
 * covers exactly what it spells.
 */
export async function connectSearchConsoleAction(formData: FormData): Promise<never> {
  const siteId = String(formData.get("site_id") ?? "").trim();
  const propertyRef = String(formData.get("property_ref") ?? "").trim();
  if (!siteId || !propertyRef) redirect(`${PAGE}?error=site_and_property_required`);

  let authorizationUrl: string;
  try {
    const result = await apiJson<AuthorizationEnvelope>(
      `/v1/sites/${siteId}/connectors/google_search_console/authorize`,
      {method: "POST", body: JSON.stringify({property_ref: propertyRef})},
    );
    authorizationUrl = result.data.authorization_url;
  } catch (error) {
    failure(error);
  }
  redirect(authorizationUrl);
}
