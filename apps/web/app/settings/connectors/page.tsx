import Link from "next/link";
import type {Metadata} from "next";

import type {Site} from "@/app/pilot/model";
import {redirect} from "next/navigation";

import {ApiError, apiJson, isMissingTenant} from "@/lib/server-api";

import {
  connectAnalyticsAction,
  connectRepositoryAction,
  connectSearchConsoleAction,
} from "./actions";

type PageProps = {searchParams: Promise<{google?: string; error?: string}>};

export const metadata: Metadata = {
  title: "Connector Status",
  robots: {index: false, follow: false},
};

type Connector = {
  id: string;
  type: string;
  status: string;
  external_account_ref: string | null;
};

const REASONS: Record<string, string> = {
  analytics_property_not_authorized:
    "That property is not one the Google account you consented with can see.",
  analytics_property_site_mismatch:
    "None of that property's data streams collect from this site's host, so it cannot be bound to it.",
  google_analytics_connector_not_configured:
    "Google connectors are not configured on this deployment.",
  site_and_property_required: "Choose a site and enter a property.",
  search_console_property_not_authorized:
    "That property is not one the Google account you consented with can see. Check it is verified in Search Console under that exact form.",
  property_site_mismatch:
    "That property does not cover this site's host. A domain property and a URL-prefix property are different properties.",
  google_connector_not_configured:
    "Google connectors are not configured on this deployment.",
  site_not_verified: "This site has not completed host verification, so no property can be bound to it.",
  repository_and_token_required: "Enter the repository and a token that can read it.",
  github_connector_not_configured:
    "GitHub connectors are not configured on this deployment.",
  github_repository_unreachable:
    "That repository could not be read with that token. Check the name, the branch, and that the token has contents access to it.",
  oauth_scope_mismatch:
    "Google granted different scopes than were asked for. Start the consent again and accept everything requested.",
};

function label(status: string | undefined): string {
  return status ? status.replaceAll("_", " ") : "Not connected";
}

export default async function ConnectorsPage({searchParams}: PageProps) {
  const query = await searchParams;

  let sites: Site[] = [];
  let connectorsBySite = new Map<string, Connector[]>();
  let loadError: string | null = null;
  try {
    sites = (await apiJson<{data: Site[]}>("/v1/sites")).data;
    const loaded = await Promise.all(
      sites.map(async (site) => {
        const body = await apiJson<{data: Connector[]}>(`/v1/sites/${site.id}/connectors`);
        return [site.id, body.data] as const;
      }),
    );
    connectorsBySite = new Map(loaded);
  } catch (error) {
    // A brand-new account is in no workspace yet. Rendering that as a load
    // failure shows a first-time visitor the string `no_tenant_membership`,
    // which names nothing they can act on; the page that fixes it does.
    if (isMissingTenant(error)) redirect("/onboarding");
    loadError = error instanceof ApiError ? error.code : "unexpected-error";
  }

  const problem = query.error ? (REASONS[query.error] ?? query.error) : null;

  return (
    <main>
      <div className="mx-auto max-w-3xl px-6 py-12">
        <p className="text-sm font-semibold text-good">SEO Autopilot</p>
        <h1 className="mt-2 text-balance text-3xl font-semibold text-ink">
          Connector status
        </h1>
        <p className="mt-2 max-w-prose text-pretty text-sm text-ink-soft">
          Search Console says what a page ranks for. Analytics says what visitors did once
          they arrived. Each is bound to the site it measures, and the binding is checked
          against Google rather than taken on trust.
        </p>

        {query.google === "connected" ? (
          <p
            role="status"
            className="mt-6 rounded-[4px] border border-good-rule bg-good-soft px-4 py-3 text-sm text-good"
          >
            Google authorization completed.
          </p>
        ) : null}
        {problem ? (
          <p
            role="alert"
            className="mt-6 rounded-[4px] border border-stop-rule bg-stop-soft px-4 py-3 text-sm text-stop"
          >
            {problem}
          </p>
        ) : null}

        {loadError ? (
          <section className="mt-8 rounded-xl border border-rule bg-surface p-6 shadow-sm">
            <h2 className="text-balance text-xl font-semibold text-ink">
              Connectors are not available
            </h2>
            <p className="mt-2 text-pretty text-sm text-ink-soft">
              The API refused the request: {loadError}.
            </p>
          </section>
        ) : sites.length === 0 ? (
          <section className="mt-8 rounded-xl border border-rule bg-surface p-6 shadow-sm">
            <h2 className="text-balance text-xl font-semibold text-ink">No sites yet</h2>
            <p className="mt-2 text-pretty text-sm text-ink-soft">
              Onboard and verify a site before connecting anything to it.
            </p>
            <Link
              href="/pilot"
              className="mt-6 inline-flex rounded-[4px] border border-rule-strong bg-surface px-4 py-2.5 text-sm font-semibold text-ink"
            >
              Return to pilot
            </Link>
          </section>
        ) : (
          sites.map((site) => {
            const connectors = connectorsBySite.get(site.id) ?? [];
            const search = connectors.find((c) => c.type === "google_search_console");
            const analytics = connectors.find((c) => c.type === "google_analytics");
            const repository = connectors.find((c) => c.type === "github_repository");
            return (
              <section
                key={site.id}
                aria-labelledby={`site-${site.id}`}
                className="mt-6 rounded-xl border border-rule bg-surface p-6 shadow-sm"
              >
                <h2
                  id={`site-${site.id}`}
                  className="text-balance text-xl font-semibold text-ink"
                >
                  {site.name}
                </h2>
                <p className="text-sm text-ink-faint">{site.normalized_host}</p>

                <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-[11rem_1fr]">
                  <dt className="text-ink-faint">Search Console</dt>
                  <dd className="font-medium capitalize">{label(search?.status)}</dd>
                  <dt className="text-ink-faint">Property</dt>
                  <dd className="break-all font-mono text-xs">
                    {search?.external_account_ref ?? "—"}
                  </dd>
                  <dt className="text-ink-faint">Analytics (GA4)</dt>
                  <dd className="font-medium capitalize">{label(analytics?.status)}</dd>
                  <dt className="text-ink-faint">Property</dt>
                  <dd className="break-all font-mono text-xs">
                    {analytics?.external_account_ref ?? "—"}
                  </dd>
                  <dt className="text-ink-faint">Repository</dt>
                  <dd className="font-medium capitalize">{label(repository?.status)}</dd>
                  <dt className="text-ink-faint">Target</dt>
                  <dd className="break-all font-mono text-xs">
                    {repository?.external_account_ref ?? "—"}
                  </dd>
                </dl>

                {search?.status === "active" ? null : (
                  <form
                    action={connectSearchConsoleAction}
                    className="mt-5 flex flex-wrap items-end gap-3 border-t border-rule pt-5"
                  >
                    <input type="hidden" name="site_id" value={site.id} />
                    <div className="grow">
                      <label
                        htmlFor={`search-${site.id}`}
                        className="block text-sm font-medium text-ink"
                      >
                        Search Console property
                      </label>
                      <input
                        id={`search-${site.id}`}
                        name="property_ref"
                        required
                        autoComplete="off"
                        defaultValue={`sc-domain:${site.normalized_host}`}
                        className="mt-1 w-full rounded-[4px] border border-rule-strong bg-surface px-3 py-2 font-mono text-sm text-ink placeholder:font-sans placeholder:text-ink-faint"
                      />
                      <p className="mt-1 text-xs text-ink-faint">
                        Whichever form is verified in Search Console. A domain property
                        (<span className="font-mono">sc-domain:{site.normalized_host}</span>)
                        covers every subdomain and both protocols; a URL-prefix property
                        (<span className="font-mono">https://{site.normalized_host}/</span>)
                        covers exactly what it spells. They are different properties and
                        hold different data.
                      </p>
                    </div>
                    <button
                      type="submit"
                      className="rounded-[4px] bg-surface px-4 py-2 text-sm font-medium text-ink hover:bg-sunk"
                     data-tip="Authorise Search Console for this site through your own Google OAuth client." aria-describedby="tip-e734f40795">
                      Connect Search Console
                    </button>
                  </form>
                )}

                {repository?.status === "active" ? null : (
                  <form
                    action={connectRepositoryAction}
                    className="mt-5 border-t border-rule pt-5"
                  >
                    <input type="hidden" name="site_id" value={site.id} />
                    <p className="text-sm font-medium text-ink">Repository</p>
                    <p className="mt-1 text-xs text-ink-faint">
                      Where approved changes are written. Without it this site can be
                      measured and never changed — a proposal is only drafted after the
                      real file has been read.
                    </p>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      <label className="text-sm text-ink">
                        Repository
                        <input
                          name="repository"
                          required
                          autoComplete="off"
                          placeholder="owner/name"
                          className="mt-1 w-full rounded-[4px] border border-rule-strong bg-surface px-3 py-2 font-mono text-sm text-ink placeholder:font-sans placeholder:text-ink-faint"
                        />
                      </label>
                      <label className="text-sm text-ink">
                        Base branch
                        <input
                          name="base_branch"
                          defaultValue="main"
                          autoComplete="off"
                          className="mt-1 w-full rounded-[4px] border border-rule-strong bg-surface px-3 py-2 font-mono text-sm text-ink"
                        />
                      </label>
                      <label className="text-sm text-ink sm:col-span-2">
                        Path template
                        <input
                          name="path_template"
                          defaultValue="{path}.html"
                          autoComplete="off"
                          className="mt-1 w-full rounded-[4px] border border-rule-strong bg-surface px-3 py-2 font-mono text-sm text-ink"
                        />
                        <span className="mt-1 block text-xs font-normal text-ink-faint">
                          How a URL path becomes a file path. This cannot be inferred —
                          two sites can share one repository under different directories.
                          For <span className="font-mono">/unscramble-tool</span> stored at{" "}
                          <span className="font-mono">WordKit/unscramble-tool.html</span>,
                          use <span className="font-mono">{"WordKit/{path}.html"}</span>.
                        </span>
                      </label>
                      <label className="text-sm text-ink sm:col-span-2">
                        Access token
                        <input
                          name="access_token"
                          type="password"
                          required
                          autoComplete="off"
                          className="mt-1 w-full rounded-[4px] border border-rule-strong bg-surface px-3 py-2 font-mono text-sm text-ink"
                        />
                        <span className="mt-1 block text-xs font-normal text-ink-faint">
                          Needs contents and pull-request access to that repository.
                          Sent once over TLS, sealed, and never returned by the API.
                        </span>
                      </label>
                    </div>
                    <button
                      type="submit"
                      className="mt-3 rounded-[4px] bg-surface px-4 py-2 text-sm font-medium text-ink hover:bg-sunk"
                     data-tip="Install your GitHub App on the repository this site deploys from." aria-describedby="tip-fdd41a3bf5">
                      Connect repository
                    </button>
                  </form>
                )}

                {analytics?.status === "active" ? null : (
                  <form
                    action={connectAnalyticsAction}
                    className="mt-5 flex flex-wrap items-end gap-3 border-t border-rule pt-5"
                  >
                    <input type="hidden" name="site_id" value={site.id} />
                    <div className="grow">
                      <label
                        htmlFor={`property-${site.id}`}
                        className="block text-sm font-medium text-ink"
                      >
                        GA4 property
                      </label>
                      <input
                        id={`property-${site.id}`}
                        name="property_ref"
                        required
                        inputMode="numeric"
                        autoComplete="off"
                        placeholder="properties/123456789"
                        className="mt-1 w-full rounded-[4px] border border-rule-strong bg-surface px-3 py-2 font-mono text-sm text-ink placeholder:font-sans placeholder:text-ink-faint"
                      />
                      <p className="mt-1 text-xs text-ink-faint">
                        Admin → Property details in GA4. The numeric id alone is fine. It is
                        accepted only if one of its data streams collects from{" "}
                        {site.normalized_host}.
                      </p>
                    </div>
                    <button
                      type="submit"
                      className="rounded-[4px] bg-surface px-4 py-2 text-sm font-medium text-ink hover:bg-sunk"
                     data-tip="Authorise Google Analytics 4 for this site through your own Google OAuth client." aria-describedby="tip-e68345520a">
                      Connect analytics
                    </button>
                  </form>
                )}
              </section>
            );
          })
        )}
      </div>
    </main>
  );
}
