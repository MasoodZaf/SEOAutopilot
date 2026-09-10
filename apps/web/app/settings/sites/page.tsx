import Link from "next/link";
import {redirect} from "next/navigation";
import type {Metadata} from "next";

import type {Site} from "@/app/pilot/model";
import {apiJson, isMissingTenant} from "@/lib/server-api";

import {
  addSiteAction,
  publishViaCloudflareAction,
  readChallenge,
  refreshChallengeAction,
  verifySiteAction,
} from "./actions";

type PageProps = {
  searchParams: Promise<{
    site?: string;
    verified?: string;
    published?: string;
    error?: string;
  }>;
};

export const metadata: Metadata = {
  title: "Sites",
  robots: {index: false, follow: false},
};

const REASONS: Record<string, string> = {
  site_fields_required: "Enter a name and the site's address.",
  dns_proof_not_found:
    "The record is not visible to our resolver yet. DNS changes can take anywhere from a minute to a few hours to publish — leave it in place and check again.",
  verification_challenge_invalid:
    "That challenge has expired or was already used. Issue a new record below.",
  verification_challenge_missing:
    "There is no pending challenge in this browser. Issue a new record below.",
  cloudflare_fields_required: "Enter the Cloudflare zone ID and an API token.",
  dns_provider_connector_not_configured:
    "DNS provider automation is not enabled on this deployment. Add the record at your registrar instead.",
  dns_provider_not_supported:
    "Only Cloudflare can publish the record for you. Every other registrar works by adding the record yourself.",
  site_already_exists: "That host is already a site in this workspace.",
  unexpected_error: "That did not work. Try again.",
};

const field =
  "rounded-[4px] border border-rule-strong px-3 py-2 text-sm font-normal text-ink";
const submit =
  "self-start rounded-[4px] bg-surface px-4 py-2 text-sm font-medium text-ink hover:bg-sunk";

export default async function SitesPage({searchParams}: PageProps) {
  const query = await searchParams;

  let sites: Site[] = [];
  let loadError: string | null = null;
  try {
    sites = (await apiJson<{data: Site[]}>("/v1/sites")).data;
  } catch (error) {
    if (isMissingTenant(error)) redirect("/onboarding");
    loadError = "Your sites could not be read.";
  }

  const challenge = await readChallenge();
  const pending = sites.filter((site) => site.status !== "active");

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-10">
      <header className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold tracking-tight text-ink">Your sites</h1>
        <p className="text-pretty text-sm text-ink-soft">
          Add a site you control, then prove it with a DNS record. Nothing is crawled and no
          connector will bind until that proof exists — which is what stops anybody pointing
          this at a domain that is not theirs.
        </p>
      </header>

      {query.verified ? (
        <p className="rounded-[4px] border border-good-rule bg-good-soft px-3 py-2 text-sm text-good">
          Verified. You can connect Search Console and a repository to it now.
        </p>
      ) : null}
      {query.published ? (
        <p className="rounded-[4px] border border-good-rule bg-good-soft px-3 py-2 text-sm text-good">
          Cloudflare published the record. Give it a moment, then check the record below.
        </p>
      ) : null}
      {query.error ? (
        <p
          role="alert"
          className="rounded-[4px] border border-stop-rule bg-stop-soft px-3 py-2 text-sm text-stop"
        >
          {REASONS[query.error] ?? "That did not work. Try again."}
        </p>
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
        aria-labelledby="add-heading"
        className="flex flex-col gap-4 rounded-[4px] border border-rule bg-surface p-5"
      >
        <div>
          <h2 id="add-heading" className="text-base font-semibold text-ink">
            Add a site
          </h2>
          <p className="mt-1 text-sm text-ink-soft">
            The address is the origin you want indexed — scheme and host, no path.
          </p>
        </div>
        <form action={addSiteAction} className="flex flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm font-medium text-ink">
              Name
              <input name="name" required maxLength={200} placeholder="Acme" className={field} />
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-ink">
              Address
              <input
                name="canonical_origin"
                required
                inputMode="url"
                placeholder="https://acme.com"
                className={field}
              />
            </label>
          </div>
          <button type="submit" className={submit} data-tip="Register the domain and issue a one-time TXT record proving you control it." aria-describedby="tip-b6ef7312e7">
            Add site and issue DNS record
          </button>
        </form>
      </section>

      {challenge ? (
        <section
          aria-labelledby="verify-heading"
          className="flex flex-col gap-4 rounded-[4px] border border-warn-rule bg-warn-soft p-5"
        >
          <div>
            <h2 id="verify-heading" className="text-base font-semibold text-ink">
              Publish this DNS record
            </h2>
            <p className="mt-1 text-pretty text-sm text-ink-soft">
              Add the TXT record below at whoever hosts your DNS — Cloudflare, Route&nbsp;53,
              GoDaddy, Namecheap, your own BIND zone, it makes no difference. Then come back and
              check it.
            </p>
          </div>

          <dl className="grid gap-3 rounded-[4px] border border-warn-rule bg-surface p-4 text-sm sm:grid-cols-[7rem_1fr]">
            <dt className="font-medium text-ink-soft">Type</dt>
            <dd className="font-mono text-ink">TXT</dd>
            <dt className="font-medium text-ink-soft">Name</dt>
            <dd className="font-mono break-all text-ink">{challenge.recordName}</dd>
            <dt className="font-medium text-ink-soft">Value</dt>
            <dd className="font-mono break-all text-ink">{challenge.recordValue}</dd>
          </dl>

          <p className="text-xs text-ink-soft">
            Some registrars append the domain to the name for you. If yours does, enter just{" "}
            <span className="font-mono">_seo-autopilot</span> as the name. This record stays
            claimable until {new Date(challenge.expiresAt).toLocaleString()}.
          </p>

          <div className="flex flex-wrap items-center gap-3">
            <form action={verifySiteAction}>
              <input type="hidden" name="site_id" value={challenge.siteId} />
              <button type="submit" className={submit} data-tip="Look up DNS now. Safe to retry — records can take minutes to hours to appear." aria-describedby="tip-2efb708ac7">
                Check the record
              </button>
            </form>
            <form action={refreshChallengeAction}>
              <input type="hidden" name="site_id" value={challenge.siteId} />
              <button
                type="submit"
                className="text-sm font-medium text-ink-soft underline underline-offset-2 hover:text-ink"
               data-tip="Replace the token with a fresh one. The old record stops working immediately." aria-describedby="tip-386f83ca86">
                Issue a new record
              </button>
            </form>
          </div>

          <details className="rounded-[4px] border border-warn-rule bg-surface p-4">
            <summary className="cursor-pointer text-sm font-medium text-ink">
              Optional: let Cloudflare add it for you
            </summary>
            <p className="mt-2 text-sm text-ink-soft">
              Only if this domain&rsquo;s DNS is on Cloudflare. Everyone else adds the record
              above by hand — it is the same record either way. The token needs{" "}
              <span className="font-mono text-xs">Zone / DNS / Edit</span> on this zone only.
            </p>
            <form action={publishViaCloudflareAction} className="mt-3 flex flex-col gap-3">
              <input type="hidden" name="site_id" value={challenge.siteId} />
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm font-medium text-ink">
                  Zone ID
                  <input name="zone_id" required autoComplete="off" className={field} />
                </label>
                <label className="flex flex-col gap-1 text-sm font-medium text-ink">
                  API token
                  <input
                    name="api_token"
                    type="password"
                    required
                    autoComplete="new-password"
                    className={field}
                  />
                </label>
              </div>
              <button type="submit" className={submit}>
                Publish via Cloudflare
              </button>
            </form>
          </details>
        </section>
      ) : null}

      <section aria-labelledby="list-heading" className="flex flex-col gap-3">
        <h2 id="list-heading" className="text-base font-semibold text-ink">
          Sites in this workspace
        </h2>
        {sites.length === 0 ? (
          <p className="rounded-[4px] border border-dashed border-rule-strong bg-surface px-4 py-6 text-center text-sm text-ink-soft">
            No sites yet. Add the first one above.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {sites.map((site) => (
              <li
                key={site.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-[4px] border border-rule bg-surface px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium text-ink">{site.name}</p>
                  <p className="truncate font-mono text-xs text-ink-faint">
                    {site.canonical_origin}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <span
                    className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                      site.status === "active"
                        ? "border-good-rule bg-good-soft text-good"
                        : "border-warn-rule bg-warn-soft text-warn"
                    }`}
                  >
                    {site.status === "active" ? "Verified" : "Awaiting DNS"}
                  </span>
                  {site.status === "active" ? (
                    <Link
                      href={`/pilot?site=${encodeURIComponent(site.normalized_host)}`}
                      className="text-sm font-medium text-ink-soft underline underline-offset-2 hover:text-ink"
                    >
                      Open
                    </Link>
                  ) : (
                    <form action={refreshChallengeAction}>
                      <input type="hidden" name="site_id" value={site.id} />
                      <button
                        type="submit"
                        className="text-sm font-medium text-ink-soft underline underline-offset-2 hover:text-ink"
                       data-tip="Issue a verification token for this domain and show the TXT record to publish." aria-describedby="tip-e2ae7396fa">
                        Get DNS record
                      </button>
                    </form>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        {pending.length > 0 && !challenge ? (
          <p className="text-sm text-ink-soft">
            {pending.length === 1 ? "One site is" : `${pending.length} sites are`} still waiting
            on DNS. Use &ldquo;Get DNS record&rdquo; to see what to publish.
          </p>
        ) : null}
      </section>
    </main>
  );
}
