import Link from "next/link";
import type {Metadata} from "next";
import {redirect} from "next/navigation";

import type {Site} from "@/app/pilot/model";
import {Badge, Note, Panel, PanelHead, button, inputMono} from "@/app/components/ui";
import {ApiError, apiJson, isMissingTenant} from "@/lib/server-api";

import {
  connectAnalyticsAction,
  connectRepositoryAction,
  connectSearchConsoleAction,
  disconnectAction,
} from "./actions";
import {describeDate, describeError, describeExpiry, describeStatus} from "./connection-state.mjs";

type PageProps = {
  searchParams: Promise<{
    google?: string;
    github?: string;
    disconnected?: string;
    error?: string;
  }>;
};

export const metadata: Metadata = {
  title: "Connections",
  robots: {index: false, follow: false},
};

type Connection = {
  id: string;
  site_id: string;
  type: string;
  provider_key: string | null;
  status: string;
  external_account_ref: string | null;
  config_json: Record<string, string>;
  consented_at: string | null;
  last_sync_at: string | null;
  last_checked_at: string | null;
  last_error_code: string | null;
  grant_expires_at: string | null;
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
  google_connector_not_configured: "Google connectors are not configured on this deployment.",
  google_oauth_client_not_configured:
    "This workspace has no Google OAuth client. Add one under Keys first.",
  site_not_verified:
    "This site has not completed host verification, so no property can be bound to it.",
  repository_and_token_required: "Enter the repository and a token that can read it.",
  github_connector_not_configured: "GitHub connectors are not configured on this deployment.",
  github_repository_unreachable:
    "That repository could not be read with that token. Check the name, the branch, and that the token has contents access to it.",
  oauth_scope_mismatch:
    "Google granted different scopes than were asked for. Start the consent again and accept everything requested.",
  connector_not_found: "That connection no longer exists.",
  forbidden: "Only an owner or an admin of this workspace can change its connections.",
};

export default async function ConnectorsPage({searchParams}: PageProps) {
  const query = await searchParams;

  let sites: Site[] = [];
  let connections: Connection[] = [];
  let loadError: string | null = null;
  try {
    const [siteBody, connectionBody] = await Promise.all([
      apiJson<{data: Site[]}>("/v1/sites"),
      apiJson<{data: Connection[]}>("/v1/connections"),
    ]);
    sites = siteBody.data;
    connections = connectionBody.data;
  } catch (error) {
    // A brand-new account is in no workspace yet. Rendering that as a load
    // failure shows a first-time visitor the string `no_tenant_membership`,
    // which names nothing they can act on; the page that fixes it does.
    if (isMissingTenant(error)) redirect("/onboarding");
    loadError = error instanceof ApiError ? error.code : "unexpected-error";
  }

  const problem = query.error ? (REASONS[query.error] ?? query.error) : null;
  const broken = connections.filter((item) => describeStatus(item.status).broken);
  const find = (siteId: string, type: string) =>
    connections.find((item) => item.site_id === siteId && item.type === type);

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-10">
      <header className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold tracking-tight text-ink">Connections</h1>
        <p className="max-w-prose text-pretty text-sm text-ink-soft">
          Search Console says what a page ranks for. Analytics says what visitors did once they
          arrived. The repository is where approved changes are written. Each is bound to the
          site it serves, and every binding is checked against the provider, not taken on
          trust. Google connections are tested once a day, so one that stops working shows up
          here the day it happens.
        </p>
      </header>

      {query.google === "connected" ? (
        <Note tone="good" role="status">
          Google authorization completed.
        </Note>
      ) : null}
      {query.github === "connected" ? (
        <Note tone="good" role="status">
          Repository connected.
        </Note>
      ) : null}
      {query.disconnected ? (
        <Note role="status">
          Disconnected. The stored credential has been destroyed and nothing will read from that
          service again until it is reconnected.
        </Note>
      ) : null}
      {problem ? (
        <Note tone="stop" role="alert">
          {problem}
        </Note>
      ) : null}

      {broken.length > 0 ? (
        <Note tone="stop" label="Needs attention">
          {broken.length === 1
            ? "One connection has stopped working. Nothing new has arrived from it since the date shown."
            : `${broken.length} connections have stopped working. Nothing new has arrived from them since the date shown on each.`}{" "}
          Use{" "}
          <strong className="font-medium">Reconnect</strong> below; it goes straight to the
          provider&rsquo;s consent screen with the same property as before.
        </Note>
      ) : null}

      {loadError ? (
        <Panel className="p-6">
          <h2 className="text-balance text-base font-semibold text-ink">
            Connections are not available
          </h2>
          <p className="mt-2 text-pretty text-sm text-ink-soft">
            The API refused the request: {loadError}.
          </p>
        </Panel>
      ) : sites.length === 0 ? (
        <Panel className="p-6">
          <h2 className="text-balance text-base font-semibold text-ink">No sites yet</h2>
          <p className="mt-2 text-pretty text-sm text-ink-soft">
            Add and verify a site before connecting anything to it.
          </p>
          <Link href="/settings/sites" className={`${button.secondary} mt-4`}>
            Add a site
          </Link>
        </Panel>
      ) : (
        sites.map((site) => (
          <Panel key={site.id} aria-labelledby={`site-${site.id}`}>
            <PanelHead
              id={`site-${site.id}`}
              title={site.name}
              description={<span className="font-mono text-xs">{site.normalized_host}</span>}
            />
            <ServiceRow
              title="Search Console"
              refLabel="Property"
              connection={find(site.id, "google_search_console")}
              reconnect={connectSearchConsoleAction}
              connect={
                <SearchConsoleForm
                  siteId={site.id}
                  host={site.normalized_host}
                  previous={find(site.id, "google_search_console")?.external_account_ref}
                />
              }
            />
            <ServiceRow
              title="Analytics (GA4)"
              refLabel="Property"
              connection={find(site.id, "google_analytics")}
              reconnect={connectAnalyticsAction}
              connect={
                <AnalyticsForm
                  siteId={site.id}
                  host={site.normalized_host}
                  previous={find(site.id, "google_analytics")?.external_account_ref}
                />
              }
            />
            <ServiceRow
              title="Repository"
              refLabel="Repository"
              connection={find(site.id, "github_repository")}
              connect={<RepositoryForm siteId={site.id} />}
            />
          </Panel>
        ))
      )}

      <Note label="Disconnecting">
        Disconnect destroys the credential this workspace holds for one site and one service.
        It does not touch the provider, because revoking at Google removes this application
        from the whole Google account and would disconnect every other site connected
        through it. To remove access at the provider as well, use{" "}
        <a
          href="https://myaccount.google.com/permissions"
          className={button.quiet}
          target="_blank"
          rel="noreferrer"
        >
          your Google account&rsquo;s third-party access
        </a>{" "}
        or{" "}
        <a
          href="https://github.com/settings/personal-access-tokens"
          className={button.quiet}
          target="_blank"
          rel="noreferrer"
        >
          your GitHub tokens
        </a>
        .
      </Note>
    </main>
  );
}

// Spelled out so Tailwind sees each class; an interpolated name is never built.
const EXPIRY_INK: Record<string, string | undefined> = {warn: "text-warn", stop: "text-stop"};

function ServiceRow({
  title,
  refLabel,
  connection,
  reconnect,
  connect,
}: {
  title: string;
  refLabel: string;
  connection: Connection | undefined;
  /** Present for services that can be re-consented with what they already hold. */
  reconnect?: (formData: FormData) => Promise<never>;
  connect: React.ReactNode;
}) {
  const state = describeStatus(connection?.status);
  const active = connection?.status === "active";
  const expiry = describeExpiry(connection?.grant_expires_at);
  const error = describeError(connection?.last_error_code);
  const canReconnect =
    state.broken && reconnect !== undefined && Boolean(connection?.external_account_ref);

  const facts: [string, React.ReactNode][] = [];
  if (connection?.external_account_ref) {
    facts.push([
      refLabel,
      <span key="ref" className="font-mono text-xs break-all">
        {connection.external_account_ref}
      </span>,
    ]);
  }
  if (connection?.consented_at && connection.status !== "revoked") {
    facts.push(["Connected", describeDate(connection.consented_at)]);
  }
  if (connection?.last_sync_at) {
    facts.push(["Last data received", describeDate(connection.last_sync_at)]);
  }
  if (connection?.last_checked_at && connection.status !== "revoked") {
    facts.push(["Last confirmed working", describeDate(connection.last_checked_at)]);
  }
  if (expiry) {
    facts.push([
      "Google sign-in",
      <span key="expiry" className={EXPIRY_INK[expiry.tone]}>
        {expiry.text}
      </span>,
    ]);
  }

  return (
    <div className="border-b border-rule px-5 py-4 last:border-b-0">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold text-ink">{title}</h3>
          <Badge tone={state.tone}>{state.label}</Badge>
        </div>
        {canReconnect && connection ? (
          <form action={reconnect}>
            <input type="hidden" name="site_id" value={connection.site_id} />
            <input
              type="hidden"
              name="property_ref"
              value={connection.external_account_ref ?? ""}
            />
            <button type="submit" className={button.primary}>
              Reconnect
            </button>
          </form>
        ) : null}
      </div>

      {facts.length > 0 ? (
        <dl className="mt-3 grid gap-x-4 gap-y-1.5 text-[13px] sm:grid-cols-[11rem_1fr]">
          {facts.map(([term, value]) => (
            <div key={term} className="contents">
              <dt className="text-ink-faint">{term}</dt>
              <dd className="text-ink">{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {error && connection?.status !== "revoked" ? (
        <p className={`mt-3 text-pretty text-[13px] ${state.broken ? "text-stop" : "text-ink-soft"}`}>
          {error}
        </p>
      ) : null}

      {active && connection ? (
        <details className="mt-3">
          <summary className={`${button.danger} cursor-pointer list-none`}>Disconnect</summary>
          <form action={disconnectAction} className="mt-2 flex flex-wrap items-center gap-3">
            <input type="hidden" name="connector_id" value={connection.id} />
            <p className="text-pretty text-[13px] text-ink-soft">
              Stop reading {title} for this site and destroy the stored credential. Other sites
              are not affected.
            </p>
            <button type="submit" className={button.secondary}>
              Disconnect {title}
            </button>
          </form>
        </details>
      ) : null}

      {active || canReconnect ? null : <div className="mt-4">{connect}</div>}
    </div>
  );
}

function SearchConsoleForm({
  siteId,
  host,
  previous,
}: {
  siteId: string;
  host: string;
  previous: string | null | undefined;
}) {
  return (
    <form action={connectSearchConsoleAction} className="flex flex-wrap items-end gap-3">
      <input type="hidden" name="site_id" value={siteId} />
      <div className="grow">
        <label htmlFor={`search-${siteId}`} className="block text-[13px] font-medium text-ink">
          Search Console property
        </label>
        <input
          id={`search-${siteId}`}
          name="property_ref"
          required
          autoComplete="off"
          defaultValue={previous ?? `sc-domain:${host}`}
          className={`${inputMono} mt-1`}
        />
        <p className="mt-1 text-xs text-ink-faint">
          Whichever form is verified in Search Console. A domain property (
          <span className="font-mono">sc-domain:{host}</span>) covers every subdomain and both
          protocols; a URL-prefix property (<span className="font-mono">https://{host}/</span>)
          covers exactly what it spells. They are different properties and hold different data.
        </p>
      </div>
      <button
        type="submit"
        className={button.secondary}
        data-tip="Authorise Search Console for this site through your own Google OAuth client."
        aria-describedby="tip-e734f40795"
      >
        Connect Search Console
      </button>
    </form>
  );
}

function AnalyticsForm({
  siteId,
  host,
  previous,
}: {
  siteId: string;
  host: string;
  previous: string | null | undefined;
}) {
  return (
    <form action={connectAnalyticsAction} className="flex flex-wrap items-end gap-3">
      <input type="hidden" name="site_id" value={siteId} />
      <div className="grow">
        <label htmlFor={`property-${siteId}`} className="block text-[13px] font-medium text-ink">
          GA4 property
        </label>
        <input
          id={`property-${siteId}`}
          name="property_ref"
          required
          inputMode="numeric"
          autoComplete="off"
          defaultValue={previous ?? undefined}
          placeholder="properties/123456789"
          className={`${inputMono} mt-1`}
        />
        <p className="mt-1 text-xs text-ink-faint">
          Admin → Property details in GA4. It is accepted only if one of its data streams
          collects from {host}.
        </p>
      </div>
      <button
        type="submit"
        className={button.secondary}
        data-tip="Authorise Google Analytics 4 for this site through your own Google OAuth client."
        aria-describedby="tip-e68345520a"
      >
        Connect analytics
      </button>
    </form>
  );
}

function RepositoryForm({siteId}: {siteId: string}) {
  return (
    <form action={connectRepositoryAction}>
      <input type="hidden" name="site_id" value={siteId} />
      <p className="text-xs text-ink-faint">
        Where approved changes are written. Without it this site can be measured and never
        changed — a proposal is only drafted after the real file has been read.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-[13px] text-ink">
          Repository
          <input
            name="repository"
            required
            autoComplete="off"
            placeholder="owner/name"
            className={`${inputMono} mt-1`}
          />
        </label>
        <label className="text-[13px] text-ink">
          Base branch
          <input
            name="base_branch"
            defaultValue="main"
            autoComplete="off"
            className={`${inputMono} mt-1`}
          />
        </label>
        <label className="text-[13px] text-ink sm:col-span-2">
          Path template
          <input
            name="path_template"
            defaultValue="{path}.html"
            autoComplete="off"
            className={`${inputMono} mt-1`}
          />
          <span className="mt-1 block text-xs font-normal text-ink-faint">
            How a URL path becomes a file path. This cannot be inferred — two sites can share one
            repository under different directories. For{" "}
            <span className="font-mono">/unscramble-tool</span> stored at{" "}
            <span className="font-mono">WordKit/unscramble-tool.html</span>, use{" "}
            <span className="font-mono">{"WordKit/{path}.html"}</span>.
          </span>
        </label>
        <label className="text-[13px] text-ink sm:col-span-2">
          Access token
          <input
            name="access_token"
            type="password"
            required
            autoComplete="off"
            className={`${inputMono} mt-1`}
          />
          <span className="mt-1 block text-xs font-normal text-ink-faint">
            Needs contents and pull-request access to that repository. Sent once over TLS,
            sealed, and never returned by the API.
          </span>
        </label>
      </div>
      <button
        type="submit"
        className={`${button.secondary} mt-3`}
        data-tip="Install your GitHub App on the repository this site deploys from."
        aria-describedby="tip-fdd41a3bf5"
      >
        Connect repository
      </button>
    </form>
  );
}
