import Link from "next/link";
import type {Metadata} from "next";
import {redirect} from "next/navigation";

import type {Site} from "@/app/pilot/model";
import {GoogleClientGuide} from "@/app/components/google-client-guide";
import {Badge, Note, Panel, PanelHead, button, inputMono} from "@/app/components/ui";
import {ApiError, apiJson, isMissingTenant} from "@/lib/server-api";

import {
  connectAnalyticsAction,
  connectGitHubAppAction,
  connectGoogleAction,
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
    linked?: string;
    unmatched?: string;
    partial?: string;
  }>;
};

export const metadata: Metadata = {
  title: "Connections",
  robots: {index: false, follow: false},
};

type ClientCheck = {
  status: "ok" | "redirect_uri_mismatch" | "client_unknown" | "undetermined" | "not_configured";
  source: "tenant" | "platform" | "none";
  redirect_uri: string;
  client_id: string | null;
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
  google_no_matching_properties:
    "That Google account cannot see a Search Console or GA4 property for any of your sites that is not already connected. Sign in with the account that owns the properties, or check they are verified in Search Console.",
  oauth_state_invalid:
    "That sign-in link had expired or was already used. Start again from this page.",
  installation_not_completed: "The GitHub install was cancelled, so nothing was connected.",
  installation_state_invalid:
    "That GitHub install link had expired or was already used. Start again from this page.",
  github_app_not_configured:
    "This workspace has no GitHub App yet. Add one under Keys, or use an access token for now.",
  github_repository_invalid: "Enter the repository as owner/name.",
  github_repository_not_installed:
    "The GitHub App was installed, but not on that repository. Install it again and include the repository.",
  github_installation_permissions_insufficient:
    "The GitHub App does not have write access to contents and pull requests. Update the App's permissions, then install again.",
  forbidden: "Only an owner or an admin of this workspace can change its connections.",
};

export default async function ConnectorsPage({searchParams}: PageProps) {
  const query = await searchParams;

  let sites: Site[] = [];
  let connections: Connection[] = [];
  let abandoned = 0;
  let clientCheck: ClientCheck | null = null;
  let githubAppReady = false;
  let loadError: string | null = null;
  try {
    const [siteBody, connectionBody] = await Promise.all([
      apiJson<{data: Site[]}>("/v1/sites"),
      apiJson<{data: Connection[]; meta: {abandoned_consents?: number}}>("/v1/connections"),
    ]);
    sites = siteBody.data;
    connections = connectionBody.data;
    abandoned = connectionBody.meta?.abandoned_consents ?? 0;
    // Only when there is something to explain. This asks Google a question
    // over the network, and putting that on every render of the page would
    // be paying for a diagnosis nobody needs.
    if (abandoned > 0) {
      clientCheck = await apiJson<ClientCheck>(
        "/v1/tenant/credentials/google_oauth_client/check",
      ).catch(() => null);
    }
    // Whether a one-step install can be offered. Unknown counts as no: the
    // token form still works, and offering an install that cannot start is a
    // button that only ever produces an error.
    githubAppReady = await apiJson<{data: {provider: string; source: string}[]}>(
      "/v1/tenant/credentials",
    )
      .then((body) =>
        body.data.some((item) => item.provider === "github_app" && item.source !== "none"),
      )
      .catch(() => false);
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
          <GoogleResult linked={query.linked} unmatched={query.unmatched} partial={query.partial} />
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

      {/*
        A consent that was started and never finished. The provider refused it
        at its own door, so nothing reached us to log -- this banner and the
        guide under it are the only place that failure is ever put into words.
      */}
      {abandoned > 0 && clientCheck?.status === "redirect_uri_mismatch" ? (
        <GoogleClientGuide
          redirectUri={clientCheck.redirect_uri}
          label="Google is refusing this workspace's sign-in"
        />
      ) : abandoned > 0 && clientCheck?.status === "client_unknown" ? (
        <Note tone="stop" label="Google is refusing this workspace's sign-in">
          Google does not recognise the OAuth client this workspace is set up with, so the
          sign-in never reaches a consent screen. Check it still exists under{" "}
          <strong className="font-medium">Keys</strong>, or remove it there to use this
          deployment&rsquo;s shared client instead.
        </Note>
      ) : abandoned > 0 ? (
        <Note tone="warn" label="A sign-in did not finish">
          {abandoned === 1
            ? "A Google sign-in was started from here and never came back, so nothing was connected."
            : `${abandoned} Google sign-ins were started from here and never came back, so nothing was connected.`}{" "}
          If you cancelled it, nothing is wrong and this clears on the next successful
          connection. If you were shown an error at Google instead, tell us what it said — the
          refusal happens entirely at Google and we are not told the reason.
        </Note>
      ) : null}

      {broken.length > 0 ? (
        <Note tone="stop" label="Needs attention">
          {broken.length === 1
            ? "One connection has stopped working. Nothing new has arrived from it since the date shown."
            : `${broken.length} connections have stopped working. Nothing new has arrived from them since the date shown on each.`}{" "}
          Use <strong className="font-medium">Reconnect</strong> below. For Google, one
          sign-in reconnects every site at once, each to the property it read before.
        </Note>
      ) : null}

      {!loadError && sites.length > 0 ? (
        <Panel aria-labelledby="google-heading">
          <PanelHead
            id="google-heading"
            title="Google Search Console and Analytics"
            description={
              <>
                One Google sign-in connects both, for every verified site. Each site is matched
                to the property that covers its address, so there is nothing to type. Sites
                already connected keep the property they read now; use this again at any time
                to reconnect or to pick up a site you have just added.
              </>
            }
            aside={
              <form action={connectGoogleAction}>
                <button
                  type="submit"
                  className={button.primary}
                  data-tip="Sign in with Google once. Search Console and Analytics are connected for every verified site whose property that account can see."
                  aria-describedby="tip-dd1bb7f2b5"
                >
                  Connect Google
                </button>
              </form>
            }
          />
        </Panel>
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
              reconnect={connectGoogleAction}
              connect={
                <ManualGoogle>
                  <SearchConsoleForm
                    siteId={site.id}
                    host={site.normalized_host}
                    previous={find(site.id, "google_search_console")?.external_account_ref}
                  />
                </ManualGoogle>
              }
            />
            <ServiceRow
              title="Analytics (GA4)"
              refLabel="Property"
              connection={find(site.id, "google_analytics")}
              reconnect={connectGoogleAction}
              connect={
                <ManualGoogle>
                  <AnalyticsForm
                    siteId={site.id}
                    host={site.normalized_host}
                    previous={find(site.id, "google_analytics")?.external_account_ref}
                  />
                </ManualGoogle>
              }
            />
            <ServiceRow
              title="Repository"
              refLabel="Repository"
              connection={find(site.id, "github_repository")}
              connect={<RepositoryConnect siteId={site.id} appReady={githubAppReady} />}
              extra={
                githubAppReady &&
                find(site.id, "github_repository")?.provider_key === "github_pat" ? (
                  <details className="mt-3">
                    <summary className={`${button.quiet} cursor-pointer list-none`}>
                      Switch to the GitHub App
                    </summary>
                    <p className="mt-2 text-pretty text-[13px] text-ink-soft">
                      Deploys keep using the stored token until the install finishes, and the
                      token is destroyed once it does.
                    </p>
                    <div className="mt-3">
                      <GitHubAppForm
                        siteId={site.id}
                        previous={find(site.id, "github_repository")}
                      />
                    </div>
                  </details>
                ) : null
              }
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
  extra,
}: {
  title: string;
  refLabel: string;
  connection: Connection | undefined;
  /** Present for services that can be re-consented without typing anything. */
  reconnect?: (formData: FormData) => Promise<never>;
  connect: React.ReactNode;
  /** Shown under a working connection, before Disconnect. */
  extra?: React.ReactNode;
}) {
  const state = describeStatus(connection?.status);
  const active = connection?.status === "active";
  const expiry = describeExpiry(connection?.grant_expires_at);
  const error = describeError(connection?.last_error_code);
  const canReconnect = state.broken && reconnect !== undefined;

  const facts: [string, React.ReactNode][] = [];
  if (connection?.external_account_ref) {
    facts.push([
      refLabel,
      <span key="ref" className="font-mono text-xs break-all">
        {connection.external_account_ref}
      </span>,
    ]);
  }
  if (connection?.provider_key === "github_app" || connection?.provider_key === "github_pat") {
    const account = connection.config_json?.account;
    facts.push([
      "Access",
      connection.provider_key === "github_app"
        ? `GitHub App${account ? ` installed on ${account}` : ""}`
        : "Access token",
    ]);
  }
  if (connection?.config_json?.path_template) {
    facts.push([
      "Files",
      <span key="files" className="font-mono text-xs break-all">
        {connection.config_json.base_branch ?? "main"}:{connection.config_json.path_template}
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
        {canReconnect ? (
          <form action={reconnect}>
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

      {active ? extra : null}

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
        data-tip="Store an access token for this repository. Prefer the GitHub App when you can install it."
        aria-describedby="tip-3f34c967d8"
      >
        Connect with a token
      </button>
    </form>
  );
}

function GoogleResult({
  linked,
  unmatched,
  partial,
}: {
  linked?: string;
  unmatched?: string;
  partial?: string;
}) {
  const count = Number(linked ?? "");
  const missing = Number(unmatched ?? "");
  if (!Number.isFinite(count) || linked === undefined) return <>Google authorization completed.</>;
  return (
    <>
      Google connected: {count === 1 ? "1 connection" : `${count} connections`} linked.
      {missing > 0
        ? ` ${missing === 1 ? "1 other has" : `${missing} others have`} no property this Google account can see; they are marked Not connected below.`
        : ""}
      {partial
        ? " Only one of the two permissions was granted. Use Connect Google again and leave both ticked to add the other service."
        : ""}
    </>
  );
}

/** The typed-property form, for when the automatic match is not the one wanted. */
function ManualGoogle({children}: {children: React.ReactNode}) {
  return (
    <>
      <p className="text-pretty text-[13px] text-ink-soft">
        <strong className="font-medium text-ink">Connect Google</strong> at the top of this page
        finds this site&rsquo;s property for you.
      </p>
      <details className="mt-2">
        <summary className={`${button.quiet} cursor-pointer list-none`}>
          Choose a property yourself
        </summary>
        <div className="mt-3">{children}</div>
      </details>
    </>
  );
}

function RepositoryConnect({siteId, appReady}: {siteId: string; appReady: boolean}) {
  if (!appReady) {
    return (
      <>
        <p className="mb-3 text-pretty text-[13px] text-ink-soft">
          Add a GitHub App under{" "}
          <Link href="/settings/keys" className={button.quiet}>
            Keys
          </Link>{" "}
          to connect a repository in one step, with no token to copy. Until then, an access
          token works.
        </p>
        <RepositoryForm siteId={siteId} />
      </>
    );
  }
  return (
    <>
      <GitHubAppForm siteId={siteId} />
      <details className="mt-4">
        <summary className={`${button.quiet} cursor-pointer list-none`}>
          Use an access token instead
        </summary>
        <div className="mt-3">
          <RepositoryForm siteId={siteId} />
        </div>
      </details>
    </>
  );
}

function GitHubAppForm({siteId, previous}: {siteId: string; previous?: Connection}) {
  return (
    <form action={connectGitHubAppAction}>
      <input type="hidden" name="site_id" value={siteId} />
      <p className="text-xs text-ink-faint">
        Where approved changes are written. Name the repository, then approve the GitHub App
        on it on GitHub&rsquo;s page. No token is copied or stored.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-[13px] text-ink">
          Repository
          <input
            name="repository"
            required
            autoComplete="off"
            placeholder="owner/name"
            defaultValue={previous?.external_account_ref ?? undefined}
            className={`${inputMono} mt-1`}
          />
        </label>
        <label className="text-[13px] text-ink">
          Base branch
          <input
            name="base_branch"
            defaultValue={previous?.config_json?.base_branch ?? "main"}
            autoComplete="off"
            className={`${inputMono} mt-1`}
          />
        </label>
        <label className="text-[13px] text-ink sm:col-span-2">
          Path template
          <input
            name="path_template"
            defaultValue={previous?.config_json?.path_template ?? "{path}.html"}
            autoComplete="off"
            className={`${inputMono} mt-1`}
          />
          <span className="mt-1 block text-xs font-normal text-ink-faint">
            How a URL path becomes a file path. For{" "}
            <span className="font-mono">/unscramble-tool</span> stored at{" "}
            <span className="font-mono">WordKit/unscramble-tool.html</span>, use{" "}
            <span className="font-mono">{"WordKit/{path}.html"}</span>.
          </span>
        </label>
      </div>
      <button
        type="submit"
        className={`${button.primary} mt-3`}
        data-tip="Install your GitHub App on the repository this site deploys from."
        aria-describedby="tip-fdd41a3bf5"
      >
        Connect GitHub
      </button>
    </form>
  );
}
