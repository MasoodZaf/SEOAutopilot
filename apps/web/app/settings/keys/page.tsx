import {redirect} from "next/navigation";
import type {Metadata} from "next";

import {GoogleClientGuide} from "@/app/components/google-client-guide";
import {apiJson, isMissingTenant} from "@/lib/server-api";

import {revokeCredentialAction, saveGithubAppAction, saveGoogleClientAction} from "./actions";

type PageProps = {
  searchParams: Promise<{saved?: string; revoked?: string; error?: string; created?: string}>;
};

export const metadata: Metadata = {
  title: "Provider Keys",
  robots: {index: false, follow: false},
};

type Credential = {
  provider: string;
  source: "tenant" | "platform" | "none";
  config: Record<string, string>;
  configured_at: string | null;
};

const REASONS: Record<string, string> = {
  google_client_incomplete: "Enter both the client ID and the client secret.",
  google_client_id_invalid:
    "That does not look like an OAuth client ID. It ends in .apps.googleusercontent.com — the project number and the API key are different values.",
  google_client_secret_invalid: "That client secret is too short to be one.",
  google_client_redirect_uri_not_registered:
    "Google refuses this client: our callback is not one of its authorised redirect URIs. Nothing was saved. The steps below fix it.",
  google_client_unknown:
    "Google does not recognise that client ID. Check it was copied whole from the OAuth client you mean to use, and that the client has not been deleted.",
  github_app_incomplete: "Enter the app ID, the app slug and the private key.",
  github_app_client_incomplete: "Enter both the client ID and the client secret, or neither.",
  github_app_id_invalid: "The GitHub app ID is a number, shown on the app's settings page.",
  github_app_slug_invalid: "Enter the app slug — the last part of the app's public URL.",
  github_app_private_key_invalid:
    "That private key could not be read. Paste the whole .pem file, including the BEGIN and END lines.",
  tenant_credentials_not_configured:
    "This deployment has no secret encryption key configured, so credentials cannot be stored.",
  tenant_credential_not_found: "There was nothing stored to remove.",
  forbidden: "Only an owner or an admin of this workspace can change its credentials.",
};

const SOURCE_NOTE: Record<Credential["source"], string> = {
  tenant: "Your own credential is in use.",
  // Deliberately not a nudge any more. This used to end "Store your own
  // below", and tenants did -- into a Google Cloud project they then had to
  // configure correctly, with a redirect URI that fails the whole connection
  // if it is off by one character. The shared client needs no setup and
  // works on the first click, so it is the recommendation, not the fallback.
  platform:
    "Using this deployment's shared Google client. Nothing to set up, and this is the normal way to run. The consent screen names the operator's application and the API quota is shared.",
  none: "Nothing is configured, so this connector cannot be used yet.",
};

function find(credentials: Credential[], provider: string): Credential | undefined {
  return credentials.find((item) => item.provider === provider);
}

/**
 * The redirect URI to register with the provider, read from the deployment.
 *
 * Shown rather than documented. A redirect URI that disagrees with the server
 * by one character fails at the provider with `redirect_uri_mismatch`, which
 * names nothing the person can connect to what they typed -- and a value
 * copied out of a runbook is exactly the one that goes stale.
 */
function Callback({label, value}: {label: string; value: string | undefined}) {
  if (!value) return null;
  return (
    <div className="rounded-[4px] border border-rule bg-sunk px-3 py-2">
      <p className="text-xs font-medium text-ink-soft">{label}</p>
      <p className="mt-1 font-mono text-xs break-all text-ink">{value}</p>
    </div>
  );
}

function SourceBadge({source}: {source: Credential["source"]}) {
  const tone =
    source === "tenant"
      ? "border-good-rule bg-good-soft text-good"
      : source === "platform"
        ? "border-warn-rule bg-warn-soft text-warn"
        : "border-rule bg-sunk text-ink-soft";
  const label = source === "tenant" ? "Yours" : source === "platform" ? "Shared" : "Not set";
  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}>{label}</span>
  );
}

const field =
  "rounded-[4px] border border-rule-strong px-3 py-2 text-sm font-normal text-ink";
const submit =
  "self-start rounded-[4px] bg-surface px-4 py-2 text-sm font-medium text-ink hover:bg-sunk";

export default async function KeysPage({searchParams}: PageProps) {
  const query = await searchParams;

  let credentials: Credential[] = [];
  let callbacks: Record<string, string> = {};
  let loadError: string | null = null;
  try {
    const body = await apiJson<{data: Credential[]; callbacks: Record<string, string>}>(
      "/v1/tenant/credentials",
    );
    credentials = body.data;
    callbacks = body.callbacks;
  } catch (error) {
    if (isMissingTenant(error)) redirect("/onboarding");
    loadError = "The stored credentials could not be read.";
  }

  const google = find(credentials, "google_oauth_client");
  const github = find(credentials, "github_app");

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-10">
      <header className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          Your provider keys
        </h1>
        <p className="text-pretty text-sm text-ink-soft">
          These belong to your workspace. Search Console, Analytics and GitHub all run through
          the credentials you store here, so the consent screens name your applications and the
          API quota you spend is your own. They are encrypted before they are written down, and
          no page ever reads one back to you — replacing one is how you change it.
        </p>
        <p className="text-pretty text-sm text-ink-soft">
          Storing your own Google client is optional, and it costs more than it looks.
          Analytics access is a scope Google reviews, and Google reviews the project that asks
          for it — so your own client eventually means your own verification, your own demo
          video, and a limit of 100 people until it passes. Leaving this empty uses the
          client this deployment already maintains, which is the ordinary way to run and needs
          no setup.
        </p>
      </header>

      {query.created ? (
        <p className="rounded-[4px] border border-good-rule bg-good-soft px-3 py-2 text-sm text-good">
          Workspace created. Add your credentials below, then add your first site.
        </p>
      ) : null}
      {query.saved ? (
        <p className="rounded-[4px] border border-good-rule bg-good-soft px-3 py-2 text-sm text-good">
          Saved. Anything you connect from now on uses it.
        </p>
      ) : null}
      {query.revoked ? (
        <p className="rounded-[4px] border border-rule bg-sunk px-3 py-2 text-sm text-ink-soft">
          Removed. This workspace is back on the deployment&rsquo;s shared client. A refresh
          token issued by your own client cannot be renewed by a different one, so each Google
          connection will ask to be reconnected once — after that it stays connected.
        </p>
      ) : null}
      {query.error ? (
        <p
          role="alert"
          className="rounded-[4px] border border-stop-rule bg-stop-soft px-3 py-2 text-sm text-stop"
        >
          {REASONS[query.error] ?? "That could not be saved."}
        </p>
      ) : null}
      {query.error === "google_client_redirect_uri_not_registered" ? (
        <GoogleClientGuide redirectUri={callbacks.google_oauth_client ?? ""} />
      ) : null}
      {loadError ? (
        <p
          role="alert"
          className="rounded-[4px] border border-stop-rule bg-stop-soft px-3 py-2 text-sm text-stop"
        >
          {loadError}
        </p>
      ) : null}

      <section
        aria-labelledby="google-heading"
        className="flex flex-col gap-4 rounded-[4px] border border-rule bg-surface p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="google-heading" className="text-base font-semibold text-ink">
              Google OAuth client
            </h2>
            <p className="mt-1 text-sm text-ink-soft">
              Used for Search Console and Analytics 4.{" "}
              <strong className="font-medium">Most workspaces need nothing here</strong> — the
              shared client above is already connecting Google for you. Bring your own only if
              you need your own API quota, or your organisation will not consent to a
              third-party application.
            </p>
            <p className="mt-2 text-sm text-ink-soft">
              If you do: create an <strong className="font-medium">OAuth client ID</strong> of
              type <strong className="font-medium">Web application</strong> in your own Google
              Cloud project, enable the Search Console API and the Google Analytics Admin and
              Data APIs, and register the redirect URI below. We check the client with Google
              before storing it, so a save that succeeds is one that works.
            </p>
          </div>
          {google ? <SourceBadge source={google.source} /> : null}
        </div>

        {google ? (
          <p className="rounded-[4px] border border-rule bg-sunk px-3 py-2 text-sm text-ink-soft">
            {SOURCE_NOTE[google.source]}
            {google.source === "tenant" && google.config.client_id ? (
              <>
                {" "}
                <span className="font-mono text-xs break-all">{google.config.client_id}</span>
              </>
            ) : null}
          </p>
        ) : null}

        <Callback
          label="Authorised redirect URI — paste this into the Google client exactly"
          value={callbacks.google_oauth_client}
        />

        <form action={saveGoogleClientAction} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm font-medium text-ink" data-tip="From your own Google Cloud OAuth client. Ends in .apps.googleusercontent.com — not the project number and not an API key.">
            Client ID
            <input
              name="client_id"
              required
              autoComplete="off"
              placeholder="123456789012-abc.apps.googleusercontent.com"
              className={field}
            aria-describedby="tip-ebe4f0342d"/>
          </label>
          <label className="flex flex-col gap-1 text-sm font-medium text-ink" data-tip="The secret beside that client ID in Google Cloud. Stored encrypted; no page ever reads it back to you.">
            Client secret
            <input
              name="client_secret"
              type="password"
              required
              autoComplete="new-password"
              className={field}
            aria-describedby="tip-77738fa850"/>
          </label>
          <button type="submit" className={submit}>
            {google?.source === "tenant" ? "Replace Google client" : "Save Google client"}
          </button>
        </form>

        {google?.source === "tenant" ? (
          <form action={revokeCredentialAction}>
            <input type="hidden" name="provider" value="google_oauth_client" />
            <button
              type="submit"
              className="text-sm font-medium text-stop underline underline-offset-2 hover:text-stop"
             data-tip="Revoke your stored credential and go back to this deployment's shared client. Each Google connection will ask to be reconnected once, because a refresh token cannot be renewed by a different client." aria-describedby="tip-9f56c4389a">
              Remove this credential
            </button>
          </form>
        ) : null}
      </section>

      <section
        aria-labelledby="github-heading"
        className="flex flex-col gap-4 rounded-[4px] border border-rule bg-surface p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="github-heading" className="text-base font-semibold text-ink">
              GitHub App
            </h2>
            <p className="mt-1 text-sm text-ink-soft">
              Optional. <strong className="font-medium">Connect GitHub</strong> under
              Connections uses this deployment&rsquo;s app, and you only sign in and pick a
              repository. Register your own app only if your organisation will not install a
              third-party one. It needs{" "}
              <strong className="font-medium">Contents: read &amp; write</strong> and{" "}
              <strong className="font-medium">Pull requests: read &amp; write</strong>, and the
              URL below as both its Callback URL and its Setup URL. No token is ever stored.
            </p>
          </div>
          {github ? <SourceBadge source={github.source} /> : null}
        </div>

        {github ? (
          <p className="rounded-[4px] border border-rule bg-sunk px-3 py-2 text-sm text-ink-soft">
            {SOURCE_NOTE[github.source]}
            {github.source === "tenant" && github.config.app_slug ? (
              <>
                {" "}
                <span className="font-mono text-xs">{github.config.app_slug}</span>
              </>
            ) : null}
          </p>
        ) : null}

        <Callback
          label="Callback URL and Setup URL — paste this into the GitHub App exactly"
          value={callbacks.github_app}
        />

        <form action={saveGithubAppAction} className="flex flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm font-medium text-ink" data-tip="The numeric ID on your GitHub App's settings page.">
              App ID
              <input
                name="app_id"
                required
                inputMode="numeric"
                autoComplete="off"
                placeholder="1234567"
                className={field}
              aria-describedby="tip-09c4eb939d"/>
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-ink" data-tip="The last part of your GitHub App's public URL.">
              App slug
              <input
                name="app_slug"
                required
                autoComplete="off"
                placeholder="my-seo-autopilot"
                className={field}
              aria-describedby="tip-c2cee2d7bc"/>
            </label>
          </div>
          <label className="flex flex-col gap-1 text-sm font-medium text-ink" data-tip="The whole .pem file your GitHub App issued, including the BEGIN and END lines.">
            Private key (.pem)
            <textarea
              name="private_key"
              required
              rows={5}
              autoComplete="off"
              placeholder="-----BEGIN RSA PRIVATE KEY-----"
              className={`${field} font-mono text-xs`}
            aria-describedby="tip-2fee17e010"/>
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm font-medium text-ink" data-tip="Shown on your GitHub App's settings page, under About.">
              Client ID
              <input
                name="client_id"
                autoComplete="off"
                placeholder="Iv23li..."
                className={field}
              aria-describedby="tip-c43e3ddcbe"/>
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-ink" data-tip="Generate one under Client secrets on the same page. It lets people sign in so only repositories they can push to are offered.">
              Client secret
              <input
                name="client_secret"
                type="password"
                autoComplete="off"
                className={field}
              aria-describedby="tip-315c078734"/>
            </label>
          </div>
          <button type="submit" className={submit}>
            {github?.source === "tenant" ? "Replace GitHub App" : "Save GitHub App"}
          </button>
        </form>

        {github?.source === "tenant" ? (
          <form action={revokeCredentialAction}>
            <input type="hidden" name="provider" value="github_app" />
            <button
              type="submit"
              className="text-sm font-medium text-stop underline underline-offset-2 hover:text-stop"
            >
              Remove this credential
            </button>
          </form>
        ) : null}
      </section>

      <section className="rounded-[4px] border border-rule bg-sunk p-5">
        <h2 className="text-sm font-semibold text-ink">What this deployment can still see</h2>
        <p className="mt-2 text-pretty text-sm text-ink-soft">
          Your credentials are encrypted with a key held by the server, because the server is
          what exchanges them with Google and GitHub. That protects them from anyone reading the
          database — a dump, a backup, a replica — but not from whoever operates this
          deployment. If that matters for the account you are connecting, use one scoped to only
          the sites and repositories you are testing with.
        </p>
      </section>
    </main>
  );
}
