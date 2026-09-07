import Link from "next/link";
import type {Metadata} from "next";

import type {Site} from "@/app/pilot/model";
import {ApiError, apiJson} from "@/lib/server-api";

import {connectAnalyticsAction} from "./actions";

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
  site_and_property_required: "Choose a site and enter a GA4 property.",
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
    loadError = error instanceof ApiError ? error.code : "unexpected-error";
  }

  const problem = query.error ? (REASONS[query.error] ?? query.error) : null;

  return (
    <main>
      <div className="mx-auto max-w-3xl px-6 py-12">
        <p className="text-sm font-semibold text-emerald-700">SEO Autopilot</p>
        <h1 className="mt-2 text-balance text-3xl font-semibold text-slate-900">
          Connector status
        </h1>
        <p className="mt-2 max-w-prose text-pretty text-sm text-slate-600">
          Search Console says what a page ranks for. Analytics says what visitors did once
          they arrived. Each is bound to the site it measures, and the binding is checked
          against Google rather than taken on trust.
        </p>

        {query.google === "connected" ? (
          <p
            role="status"
            className="mt-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900"
          >
            Google authorization completed.
          </p>
        ) : null}
        {problem ? (
          <p
            role="alert"
            className="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900"
          >
            {problem}
          </p>
        ) : null}

        {loadError ? (
          <section className="mt-8 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-balance text-xl font-semibold text-slate-900">
              Connectors are not available
            </h2>
            <p className="mt-2 text-pretty text-sm text-slate-600">
              The API refused the request: {loadError}.
            </p>
          </section>
        ) : sites.length === 0 ? (
          <section className="mt-8 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-balance text-xl font-semibold text-slate-900">No sites yet</h2>
            <p className="mt-2 text-pretty text-sm text-slate-600">
              Onboard and verify a site before connecting anything to it.
            </p>
            <Link
              href="/pilot"
              className="mt-6 inline-flex rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-800"
            >
              Return to pilot
            </Link>
          </section>
        ) : (
          sites.map((site) => {
            const connectors = connectorsBySite.get(site.id) ?? [];
            const search = connectors.find((c) => c.type === "google_search_console");
            const analytics = connectors.find((c) => c.type === "google_analytics");
            return (
              <section
                key={site.id}
                aria-labelledby={`site-${site.id}`}
                className="mt-6 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
              >
                <h2
                  id={`site-${site.id}`}
                  className="text-balance text-xl font-semibold text-slate-900"
                >
                  {site.name}
                </h2>
                <p className="text-sm text-slate-500">{site.normalized_host}</p>

                <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-[11rem_1fr]">
                  <dt className="text-slate-500">Search Console</dt>
                  <dd className="font-medium capitalize">{label(search?.status)}</dd>
                  <dt className="text-slate-500">Property</dt>
                  <dd className="break-all font-mono text-xs">
                    {search?.external_account_ref ?? "—"}
                  </dd>
                  <dt className="text-slate-500">Analytics (GA4)</dt>
                  <dd className="font-medium capitalize">{label(analytics?.status)}</dd>
                  <dt className="text-slate-500">Property</dt>
                  <dd className="break-all font-mono text-xs">
                    {analytics?.external_account_ref ?? "—"}
                  </dd>
                </dl>

                {analytics?.status === "active" ? null : (
                  <form
                    action={connectAnalyticsAction}
                    className="mt-5 flex flex-wrap items-end gap-3 border-t border-slate-200 pt-5"
                  >
                    <input type="hidden" name="site_id" value={site.id} />
                    <div className="grow">
                      <label
                        htmlFor={`property-${site.id}`}
                        className="block text-sm font-medium text-slate-800"
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
                        className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm text-slate-900 placeholder:font-sans placeholder:text-slate-400"
                      />
                      <p className="mt-1 text-xs text-slate-500">
                        Admin → Property details in GA4. The numeric id alone is fine. It is
                        accepted only if one of its data streams collects from{" "}
                        {site.normalized_host}.
                      </p>
                    </div>
                    <button
                      type="submit"
                      className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
                    >
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
