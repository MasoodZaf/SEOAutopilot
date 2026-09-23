"use server";

import {revalidatePath} from "next/cache";
import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

/**
 * Redirect, and invalidate the page being returned to.
 *
 * Without this the redirect is served from the client router cache, and a
 * redirect to the URL already on screen is a no-op navigation -- the mutation
 * lands on the API and the page silently keeps showing what it showed before.
 * See the same helper in app/pilot/actions.ts for what that cost.
 */
function redirectFresh(path: string): never {
  revalidatePath("/settings/connectors", "page");
  redirect(path);
}


const PAGE = "/settings/connectors";

type AuthorizationEnvelope = {data: {authorization_url: string}};

function failure(error: unknown): never {
  // `redirect()` throws; let its control-flow signal through so this action's
  // own redirects are not rewritten as a generic error.
  unstable_rethrow(error);
  const code = error instanceof ApiError ? error.code : "unexpected-error";
  redirectFresh(`${PAGE}?error=${encodeURIComponent(code)}`);
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
  if (!siteId || !propertyRef) redirectFresh(`${PAGE}?error=site_and_property_required`);

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
  redirectFresh(authorizationUrl);
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
  if (!siteId || !propertyRef) redirectFresh(`${PAGE}?error=site_and_property_required`);

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
  redirectFresh(authorizationUrl);
}

/**
 * Bind a site to the repository its pages are built from.
 *
 * Without this a site can be measured and never changed: the proposal drafter
 * reads the real file from the repository before it will produce an edit, so
 * an unconnected site has findings, opportunities, and no route to a proposal.
 * That was wordkitapp.com on 2026-09-08 — 13 pages sharing one heading, twelve
 * of them deriving a clean replacement, and nothing able to write it.
 *
 * The path template is the part worth explaining rather than defaulting. A
 * crawled URL path is not a repository path, and the mapping is a property of
 * how the site is built, not something that can be inferred: two sites can live
 * in one repository under different directories, which is exactly the case
 * here. `/unscramble-tool` is `WordKit/unscramble-tool.html`, and its sibling
 * site in the same repository is `CalcHive/{path}.html`.
 *
 * The token is sent once over TLS, sealed, and never returned by the API.
 */
export async function connectRepositoryAction(formData: FormData): Promise<never> {
  const siteId = String(formData.get("site_id") ?? "").trim();
  const repository = String(formData.get("repository") ?? "").trim();
  const baseBranch = String(formData.get("base_branch") ?? "").trim() || "main";
  const pathTemplate = String(formData.get("path_template") ?? "").trim() || "{path}.html";
  const accessToken = String(formData.get("access_token") ?? "").trim();
  if (!siteId || !repository || !accessToken) {
    redirectFresh(`${PAGE}?error=repository_and_token_required`);
  }

  try {
    await apiJson(`/v1/sites/${siteId}/connectors/github/token`, {
      method: "POST",
      body: JSON.stringify({
        repository,
        base_branch: baseBranch,
        path_template: pathTemplate,
        access_token: accessToken,
      }),
    });
  } catch (error) {
    failure(error);
  }
  redirectFresh(`${PAGE}?github=connected`);
}

/**
 * Stop using one connector and destroy the credential we hold for it.
 *
 * Local only, on purpose: revoking at Google removes this application from the
 * whole Google account, which would take down every other site connected
 * through it. The page links to the provider's own permissions page for that.
 */
export async function disconnectAction(formData: FormData): Promise<never> {
  const connectorId = String(formData.get("connector_id") ?? "").trim();
  if (!connectorId) redirectFresh(`${PAGE}?error=connector_not_found`);
  try {
    await apiJson(`/v1/connectors/${connectorId}/disconnect`, {method: "POST"});
  } catch (error) {
    failure(error);
  }
  redirectFresh(`${PAGE}?disconnected=1`);
}

/**
 * One Google consent for Search Console and Analytics, on every verified site.
 *
 * Nothing is typed: after consent the API asks Google which properties the
 * account can see and binds each site to the one that covers its host. It is
 * also how a broken Google connection is reconnected -- one consent repairs
 * every site at once, and a working connection is never moved to a different
 * property by it.
 */
export async function connectGoogleAction(): Promise<never> {
  let authorizationUrl: string;
  try {
    const result = await apiJson<AuthorizationEnvelope>("/v1/connectors/google/authorize", {
      method: "POST",
    });
    authorizationUrl = result.data.authorization_url;
  } catch (error) {
    failure(error);
  }
  redirectFresh(authorizationUrl);
}

/**
 * Connect a repository the way Claude or ChatGPT does: sign in with GitHub.
 *
 * GitHub asks the person to authorize the app (one click after the first
 * time) and, if the app is on none of their accounts yet, which repositories
 * it may reach. They come back to a list of the repositories they can push to
 * and pick one. Nothing is typed and no token is copied or stored.
 *
 * `install` goes to GitHub's install page first, which is how a repository
 * missing from the list is added.
 */
export async function connectGitHubAction(formData: FormData): Promise<never> {
  const siteId = String(formData.get("site_id") ?? "").trim();
  const install = formData.get("install") === "1";
  if (!siteId) redirectFresh(`${PAGE}?error=site_not_found`);

  let authorizationUrl: string;
  try {
    const result = await apiJson<AuthorizationEnvelope>(
      `/v1/sites/${siteId}/connectors/github/authorize`,
      {method: "POST", body: JSON.stringify({install})},
    );
    authorizationUrl = result.data.authorization_url;
  } catch (error) {
    failure(error);
  }
  redirectFresh(authorizationUrl);
}

/**
 * Bind the repository picked from the list GitHub produced at sign-in.
 *
 * Sent by GitHub's numeric id, never by name: the API looks it up in that
 * list and asks the installation again before anything is connected.
 */
export async function chooseRepositoryAction(formData: FormData): Promise<never> {
  const siteId = String(formData.get("site_id") ?? "").trim();
  const repositoryId = Number(formData.get("repository_id") ?? "");
  const baseBranch = String(formData.get("base_branch") ?? "").trim();
  const pathTemplate = String(formData.get("path_template") ?? "").trim() || "{path}.html";
  if (!siteId || !Number.isInteger(repositoryId) || repositoryId <= 0) {
    redirectFresh(
      `${PAGE}?github=choose&site=${encodeURIComponent(siteId)}&error=github_repository_required`,
    );
  }

  try {
    await apiJson(`/v1/sites/${siteId}/connectors/github/repository`, {
      method: "POST",
      body: JSON.stringify({
        repository_id: repositoryId,
        base_branch: baseBranch,
        path_template: pathTemplate,
      }),
    });
  } catch (error) {
    failure(error);
  }
  redirectFresh(`${PAGE}?github=connected`);
}
