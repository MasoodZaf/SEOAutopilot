import {redirect} from "next/navigation";
import type {Metadata} from "next";

import {apiJson, currentTenants, isMissingTenant} from "@/lib/server-api";

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
  github_app_incomplete: "Enter the app ID, the app slug and the private key.",
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
  platform:
    "Falling back to this deployment's shared credential. It works, but the consent screen names the operator's application and the API quota is shared. Store your own below.",
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
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-xs font-medium text-slate-700">{label}</p>
      <p className="mt-1 font-mono text-xs break-all text-slate-900">{value}</p>
    </div>
  );
}

function SourceBadge({source}: {source: Credential["source"]}) {
  const tone =
    source === "tenant"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : source === "platform"
        ? "border-amber-200 bg-amber-50 text-amber-900"
        : "border-slate-200 bg-slate-50 text-slate-600";
  const label = source === "tenant" ? "Yours" : source === "platform" ? "Shared" : "Not set";
  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}>{label}</span>
  );
}

const field =
  "rounded-md border border-slate-300 px-3 py-2 text-sm font-normal text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900";
const submit =
  "self-start rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900";

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
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">
          Your provider keys
        </h1>
        <p className="text-pretty text-sm text-slate-600">
          These belong to your workspace. Search Console, Analytics and GitHub all run through
          the credentials you store here, so the consent screens name your applications and the
          API quota you spend is your own. They are encrypted before they are written down, and
          no page ever reads one back to you — replacing one is how you change it.
        </p>
      </header>

      {query.created ? (
        <p className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          Workspace created. Add your credentials below, then add your first site.
        </p>
      ) : null}
      {query.saved ? (
        <p className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          Saved. Anything you connect from now on uses it.
        </p>
      ) : null}
      {query.revoked ? (
        <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
          Removed. Existing connectors keep working until they next need to renew a token.
        </p>
      ) : null}
      {query.error ? (
        <p
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {REASONS[query.error] ?? "That could not be saved."}
        </p>
      ) : null}
      {loadError ? (
        <p
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {loadError}
        </p>
      ) : null}

      <section
        aria-labelledby="google-heading"
        className="flex flex-col gap-4 rounded-lg border border-slate-200 bg-white p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="google-heading" className="text-base font-semibold text-slate-900">
              Google OAuth client
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Used for Search Console and Analytics 4. Create an{" "}
              <strong className="font-medium">OAuth client ID</strong> of type{" "}
              <strong className="font-medium">Web application</strong> in your own Google Cloud
              project, enable the Search Console API and the Google Analytics Admin and Data
              APIs, and add this deployment&rsquo;s callback as an authorised redirect URI.
            </p>
          </div>
          {google ? <SourceBadge source={google.source} /> : null}
        </div>

        {google ? (
          <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
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
          <label className="flex flex-col gap-1 text-sm font-medium text-slate-800">
            Client ID
            <input
              name="client_id"
              required
              autoComplete="off"
              placeholder="123456789012-abc.apps.googleusercontent.com"
              className={field}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm font-medium text-slate-800">
            Client secret
            <input
              name="client_secret"
              type="password"
              required
              autoComplete="new-password"
              className={field}
            />
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
              className="text-sm font-medium text-red-700 underline underline-offset-2 hover:text-red-800"
            >
              Remove this credential
            </button>
          </form>
        ) : null}
      </section>

      <section
        aria-labelledby="github-heading"
        className="flex flex-col gap-4 rounded-lg border border-slate-200 bg-white p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="github-heading" className="text-base font-semibold text-slate-900">
              GitHub App
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Used to open the pull requests that carry an approved change. Register a GitHub
              App under your own account or organisation with{" "}
              <strong className="font-medium">Contents: read &amp; write</strong> and{" "}
              <strong className="font-medium">Pull requests: read &amp; write</strong>. No token
              is ever stored — an installation token is minted per deployment and discarded.
            </p>
          </div>
          {github ? <SourceBadge source={github.source} /> : null}
        </div>

        {github ? (
          <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
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
          label="Callback URL — paste this into the GitHub App exactly"
          value={callbacks.github_app}
        />

        <form action={saveGithubAppAction} className="flex flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm font-medium text-slate-800">
              App ID
              <input
                name="app_id"
                required
                inputMode="numeric"
                autoComplete="off"
                placeholder="1234567"
                className={field}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-slate-800">
              App slug
              <input
                name="app_slug"
                required
                autoComplete="off"
                placeholder="my-seo-autopilot"
                className={field}
              />
            </label>
          </div>
          <label className="flex flex-col gap-1 text-sm font-medium text-slate-800">
            Private key (.pem)
            <textarea
              name="private_key"
              required
              rows={5}
              autoComplete="off"
              placeholder="-----BEGIN RSA PRIVATE KEY-----"
              className={`${field} font-mono text-xs`}
            />
          </label>
          <button type="submit" className={submit}>
            {github?.source === "tenant" ? "Replace GitHub App" : "Save GitHub App"}
          </button>
        </form>

        {github?.source === "tenant" ? (
          <form action={revokeCredentialAction}>
            <input type="hidden" name="provider" value="github_app" />
            <button
              type="submit"
              className="text-sm font-medium text-red-700 underline underline-offset-2 hover:text-red-800"
            >
              Remove this credential
            </button>
          </form>
        ) : null}
      </section>

      <section className="rounded-lg border border-slate-200 bg-slate-50 p-5">
        <h2 className="text-sm font-semibold text-slate-900">What this deployment can still see</h2>
        <p className="mt-2 text-pretty text-sm text-slate-600">
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
