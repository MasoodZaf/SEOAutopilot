import {cookies} from "next/headers";
import type {Metadata} from "next";
import {randomUUID} from "node:crypto";
import Link from "next/link";

import {redirect} from "next/navigation";

import {button} from "@/app/components/ui";
import {apiJson, isMissingTenant} from "@/lib/server-api";

import {
  approveProposalAction,
  connectDnsProvider,
  deployProposalAction,
  draftProposalAction,
  setApproverCountAction,
  setSiteModeAction,
  withdrawProposalAction,
  connectSearchConsole,
  createDnsProviderVerification,
  createCalibrationSet,
  refreshDnsChallenge,
  rollbackProposalAction,
  runPolicySimulationAction,
  startFirstCrawl,
  startPerformanceRun,
  toggleEmergencyFreezeAction,
  verifyPortfolioDns,
} from "./actions";
import {advisoryFor} from "./advisory.mjs";
import {SerpPreview} from "./components/serp-preview";
import {challengeCookie, type CalibrationRun, type Crawl, type Site} from "./model";
import {pilotPath, selectSite} from "./site-selection.mjs";

/**
 * The site the dashboard is pointed at, derived from the workspace's own
 * sites rather than from a list of three hosts compiled into the bundle.
 */
type Target = {host: string; name: string; origin: string};

type Connector = {id: string; type: string; provider_key: string | null; status: string; external_account_ref: string | null};
type SearchPerformance = {
  range_start: string;
  range_end: string;
  rows: number;
  clicks: number;
  impressions: number;
  ctr: number | null;
  position: number | null;
  is_sparse: boolean;
};
type Engagement = {
  range_start: string;
  range_end: string;
  rows: number;
  sessions: number;
  engaged_sessions: number;
  engagement_rate: number | null;
  views: number;
  key_events: number;
  top_landing_pages: {landing_page: string; sessions: number; engaged_sessions: number}[];
};
type PerformanceRun = {
  id: string;
  status: string;
  strategy: string;
  target_url: string;
  error_code: string | null;
  observation: null | {
    observed_at: string;
    lighthouse_version: string;
    performance_score: number;
    lcp_ms: number | null;
    inp_ms: number | null;
    cls: number | null;
    ttfb_ms: number | null;
  };
};
type PerformanceSummary = {
  sample_count: number;
  required_sample_count: number;
  status: string;
  median_performance_score: number | null;
  median_lcp_ms: number | null;
};
type Opportunity = {
  id: string;
  page_id: string;
  type: string;
  title: string;
  score: number;
  confidence: number;
  risk: string;
  page_url: string | null;
};
type Proposal = {
  id: string;
  title: string;
  rationale: string;
  target_type: string;
  target_path: string;
  risk: string;
  status: string;
  diff_unified: string;
  base_hash: string;
  expires_at: string;
};
type GovernanceStatus = {
  site_id: string;
  mode: string;
  autopilot_enabled: boolean;
  emergency_freeze: boolean;
  daily_change_budget: number;
  // Null means "use the risk tier's own floor", which is a different statement
  // from any number and is why the API keeps clearing it as a separate act.
  required_approver_count: number | null;
  today_deployments_count: number;
  freeze_window_start: string | null;
  freeze_window_end: string | null;
};
type Measurement = {
  id: string;
  proposal_id: string;
  confidence_score: number;
  is_sparse: boolean;
  delta_metrics: Record<string, number | string | boolean | null>;
  annotations: string[];
};

type PageProps = {
  searchParams: Promise<{
    error?: string;
    verified?: string;
    crawl?: string;
    performance?: string;
    calibration?: string;
    reviewed?: string;
    governance?: string;
    simulation?: string;
    proposal?: string;
    site?: string;
    dns_provider?: string;
  }>;
};

export const metadata: Metadata = {
  title: "Pilot Control Plane",
  robots: {index: false, follow: false},
};

const errorMessages: Record<string, string> = {
  dns_proof_not_found: "The TXT record is not visible yet. DNS changes can take a few minutes.",
  "verification-challenge-expired": "The verification record expired. Generate a fresh record.",
  "pilot-session-not-configured": "The local pilot session is not configured yet.",
  authentication_not_configured: "The local pilot session is not available.",
  site_not_verified: "Verify site ownership before connecting data or crawling.",
  calibration_run_already_open: "A calibration review set is already open.",
  no_opportunities: "Run and analyze a crawl before creating a review set.",
  calibration_evidence_not_ready: "The newest crawl and analysis must finish before a review set can be frozen.",
  crawl_already_active: "A bounded crawl is already queued or running for this site.",
  "dns-provider-connection-invalid": "Enter a zone ID and a scoped DNS-provider token.",
  dns_provider_connector_not_configured: "The DNS-provider assistant is not enabled for this environment yet.",
  dns_provider_zone_site_mismatch: "That DNS provider zone does not exactly match this site.",
  dns_provider_connector_not_active: "Connect a DNS provider before requesting a DNS record.",
  dns_provider_not_supported: "This DNS provider adapter is not available yet. Use the universal manual TXT path instead.",
  performance_run_already_active: "A mobile PageSpeed run is already queued or running.",
  performance_evidence_not_ready: "Complete a bounded crawl before requesting PageSpeed evidence.",
};

async function loadPilot(requestedHost: string | undefined): Promise<{
  target?: Target;
  sites: Site[];
  site?: Site;
  searchConsoleConnector?: Connector;
  dnsProviderConnector?: Connector;
  opportunities: Opportunity[];
  proposals: Proposal[];
  governance?: GovernanceStatus;
  measurements: Measurement[];
  calibration?: CalibrationRun;
  latestCrawl?: Crawl;
  searchPerformance?: SearchPerformance;
  engagement?: Engagement;
  performanceRun?: PerformanceRun;
  performanceSummary?: PerformanceSummary;
}> {
  try {
    const sites = await apiJson<{data: Site[]}>("/v1/sites");
    const site = selectSite(sites.data, requestedHost);
    if (!site) {
      // A workspace with no sites at all, or none matching. There is nothing to
      // show and nothing to onboard from here: adding a site is /settings/sites.
      return {sites: sites.data, opportunities: [], proposals: [], measurements: []};
    }
    const target: Target = {
      host: site.normalized_host,
      name: site.name,
      origin: site.canonical_origin,
    };
    const connectors = await apiJson<{data: Connector[]}>(`/v1/sites/${site.id}/connectors`);
    const oppResult = site.status === "active"
      ? await apiJson<{data: Opportunity[]}>(`/v1/sites/${site.id}/opportunities?limit=20&status=open`)
      : {data: []};
    
    let proposals: Proposal[] = [];
    let governance: GovernanceStatus | undefined;
    let measurements: Measurement[] = [];

    if (site.status === "active") {
      try {
        const propRes = await apiJson<{data: Proposal[]}>(`/v1/sites/${site.id}/proposals?limit=20`);
        proposals = propRes.data;
      } catch {
        proposals = [];
      }
      try {
        const govRes = await apiJson<{data: GovernanceStatus}>(`/v1/sites/${site.id}/governance`);
        governance = govRes.data;
      } catch {
        governance = undefined;
      }
      try {
        const measRes = await apiJson<{data: Measurement[]}>(`/v1/sites/${site.id}/measurements?limit=10`);
        measurements = measRes.data;
      } catch {
        measurements = [];
      }
    }

    let calibration: CalibrationRun | undefined;
    let latestCrawl: Crawl | undefined;
    let searchPerformance: SearchPerformance | undefined;
    let engagement: Engagement | undefined;
    let performanceRun: PerformanceRun | undefined;
    let performanceSummary: PerformanceSummary | undefined;
    try {
      latestCrawl = (await apiJson<{data: Crawl}>(`/v1/sites/${site.id}/crawls/latest`)).data;
    } catch (error) {
      if (!(error instanceof Error && "status" in error && error.status === 404)) throw error;
    }
    if (site.status === "active") {
      try {
        searchPerformance = (await apiJson<{data: SearchPerformance}>(`/v1/sites/${site.id}/search-performance`)).data;
      } catch {
        searchPerformance = undefined;
      }
      try {
        engagement = (await apiJson<{data: Engagement}>(`/v1/sites/${site.id}/engagement`)).data;
      } catch {
        engagement = undefined;
      }
      try {
        performanceRun = (await apiJson<{data: PerformanceRun}>(`/v1/sites/${site.id}/performance-runs/latest`)).data;
      } catch {
        performanceRun = undefined;
      }
      try {
        performanceSummary = (await apiJson<{data: PerformanceSummary}>(`/v1/sites/${site.id}/performance-summary`)).data;
      } catch {
        performanceSummary = undefined;
      }
      try {
        const runs = await apiJson<{data: CalibrationRun[]}>(`/v1/sites/${site.id}/calibrations`);
        calibration = runs.data[0];
      } catch {
        calibration = undefined;
      }
    }
    return {
      target,
      sites: sites.data,
      site,
      searchConsoleConnector: connectors.data.find((connector) => connector.type === "google_search_console"),
      dnsProviderConnector: connectors.data.find((connector) => connector.type === "dns_provider"),
      opportunities: oppResult.data,
      proposals,
      governance,
      measurements,
      calibration,
      latestCrawl,
      searchPerformance,
      engagement,
      performanceRun,
      performanceSummary,
    };
  } catch (error) {
    // Signed in and in no workspace yet is not a failure to report -- it is the
    // ordinary first minute of a new account, and the one useful answer to it is
    // the page that fixes it. Without this the fallback below renders an empty
    // dashboard offering to add a site, which is a dead end: there is no
    // workspace to add one to, so the offer fails wherever they accept it.
    if (isMissingTenant(error)) redirect("/onboarding");
    // The API is unreachable or refused. Reporting no sites is honest here --
    // we genuinely do not know what this workspace has -- and the page renders
    // that as "nothing to show" rather than as somebody else's site.
    return {sites: [], opportunities: [], proposals: [], measurements: []};
  }
}

export default async function PilotPage({searchParams}: PageProps) {
  const {error, governance: govUpdated, simulation, proposal: propMsg, site: requestedHost} = await searchParams;
  const data = await loadPilot(requestedHost);
  // A placeholder only when the workspace has no site at all -- in which case
  // the body below renders the "add a site" panel and nothing that reads these
  // fields is on screen. Keeping it non-optional avoids threading a null check
  // through every form in the page for a state that shows none of them.
  const target = data.target ?? {host: "", name: "This workspace", origin: ""};
  const cookieStore = await cookies();
  const challengeRaw = cookieStore.get(challengeCookie)?.value;
  const challenge = challengeRaw
    ? (JSON.parse(Buffer.from(challengeRaw, "base64url").toString("utf8")) as {
      record_name: string;
      record_value: string;
      expires_at: string;
      siteId: string;
      })
    : undefined;
  const selectedChallenge = challenge?.siteId === data.site?.id ? challenge : undefined;
  const performanceIdempotencyKey = randomUUID();

  return (
    <main className="min-h-dvh bg-paper text-ink">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 p-6">
      {/* Emergency Freeze Banner */}
      {data.governance?.emergency_freeze && (
        <div className="flex items-center justify-between rounded-[4px] border border-stop-rule bg-stop-soft p-4 text-stop">
          <div className="flex items-center gap-3">
            <span aria-hidden="true" className="text-xl">!</span>
            <div>
              <p className="font-semibold">EMERGENCY KILL-SWITCH ACTIVE</p>
              <p className="text-xs text-stop">Automated deployment is blocked site-wide.</p>
            </div>
          </div>
          <p className="max-w-56 text-right text-xs text-stop">An owner must complete incident review before lifting this freeze through the governed API.</p>
        </div>
      )}

      {/* The masthead in the layout carries the product name now, so this
          heading names the thing the page is actually about: the site being
          operated on. It used to read "SEO Autopilot Operations" above a
          masthead already saying "SEO Autopilot", and the host -- the one fact
          that changes what every control below does -- was a grey sentence
          underneath. */}
      <header className="flex flex-col gap-2 border-b border-rule pb-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="eyebrow">Operating on</p>
            <h1 className="mt-1 font-display text-[26px] leading-tight font-semibold tracking-tight text-ink">
              {target.host || target.name}
            </h1>
          </div>
          {data.site && data.governance && (
            <div className="flex flex-wrap items-center gap-2">
              {/* Navigation, not a status: this was drawn in the semantic green
                  reserved for "this succeeded", and its hover state repainted
                  the colour it already had. */}
              <Link
                href={`/pilot/workspace?site=${target.host}`}
                className={button.secondary}
                data-tip="Conversations, scheduled routines, keyword clusters and content briefs for this site."
                data-tip-side="bottom"
               aria-describedby="tip-4b1f1f81a7">
                Agent workspace
              </Link>
              <form action={runPolicySimulationAction}>
                <input type="hidden" name="site_host" value={target.host} />
                <button
                  type="submit"
                  className={button.secondary}
                  data-tip="Dry run: reports what the current policy would allow or block on open proposals. Changes nothing."
                  data-tip-side="bottom"
                 aria-describedby="tip-2d6f77dd1b">
                  Run policy simulation
                </button>
              </form>
              {!data.governance.emergency_freeze && (
                <form action={toggleEmergencyFreezeAction}>
                  <input type="hidden" name="site_host" value={target.host} />
                  <input type="hidden" name="current_freeze" value="false" />
                  <button
                    type="submit"
                    className={`${button.secondary} border-stop-rule bg-stop-soft text-stop`}
                    data-tip="Kill switch. Blocks every automated deployment for this site until an owner lifts it after an incident review."
                    data-tip-side="bottom"
                   aria-describedby="tip-9cb2174ecd">
                    Emergency freeze
                  </button>
                </form>
              )}
            </div>
          )}
        </div>
      </header>

      <section aria-labelledby="portfolio-heading" className="rounded-[4px] border border-rule bg-paper p-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 id="portfolio-heading" className="text-sm font-semibold text-ink">Your sites</h2>
            <p className="text-xs text-ink-faint">The same bounded workflow across every site in this workspace. Selection never grants permission to publish changes.</p>
          </div>
          <span className="mt-2 w-fit rounded border border-warn-rule bg-warn-soft px-2 py-1 text-xs font-medium text-warn sm:mt-0">
            Human consent required
          </span>
        </div>
        <nav aria-label="Sites in this workspace" className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {data.sites.map((item) => {
            const selected = item.normalized_host === target.host;
            return (
              <Link
                key={item.id}
                href={pilotPath(item.normalized_host)}
                aria-current={selected ? "page" : undefined}
                className={`rounded border p-3 outline-none focus-visible:ring-2 focus-visible:ring-white ${selected ? "border-white bg-surface" : "border-rule bg-paper hover:bg-surface"}`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-ink">{item.name}</span>
                  <span className={`rounded px-2 py-0.5 text-xs ${item.status === "active" ? "bg-good-soft text-good" : "bg-sunk text-ink-faint"}`}>
                    {item.status}
                  </span>
                </span>
                <span className="mt-1 block text-xs text-ink-faint">{item.normalized_host}</span>
              </Link>
            );
          })}
          <Link
            href="/settings/sites"
            className="rounded border border-dashed border-rule-strong p-3 text-center outline-none hover:bg-surface focus-visible:ring-2 focus-visible:ring-white"
          >
            <span className="text-sm font-semibold text-ink-soft">Add a site</span>
            <span className="mt-1 block text-xs text-ink-faint">Verify a domain you control</span>
          </Link>
        </nav>
      </section>

      {/* Notifications */}
      {error && (
        <div role="alert" className="rounded border border-stop-rule bg-stop-soft p-3 text-xs text-stop">
          {errorMessages[error] ?? `Error: ${error}`}
        </div>
      )}
      {govUpdated && <div role="status" aria-live="polite" className="rounded border border-good-rule bg-good-soft p-3 text-xs text-good">Governance settings updated.</div>}
      {simulation && <div role="status" aria-live="polite" className="rounded border border-sky-800 bg-sky-950/40 p-3 text-xs text-sky-300">Policy simulation completed successfully.</div>}
      {propMsg && <div role="status" aria-live="polite" className="rounded border border-good-rule bg-good-soft p-3 text-xs text-good">Proposal status transition applied: {propMsg}.</div>}

      {/* Governance & Autopilot Status Bar */}
      {data.governance && (
        <section className="grid grid-cols-1 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          <div className="rounded-[4px] border border-rule bg-paper p-4">
            <p className="text-xs text-ink-faint">Operation Mode</p>
            <p className="text-lg font-semibold uppercase text-ink">{data.governance.mode}</p>
            {/*
              Readable here since this dashboard was built, and changeable only
              by an operator token that no longer exists. A site could sit in
              `observe` for ever, refusing every deployment, with no way to say
              otherwise. The reason is required by the API, not decoration: this
              is recorded like a freeze.
            */}
            {data.site && (
              <form action={setSiteModeAction} className="mt-3 space-y-2">
                <input type="hidden" name="site_host" value={target.host} />
                <input type="hidden" name="site_id" value={data.site.id} />
                {/* The balloon lives on a wrapper: ::before and ::after do not
                    apply to replaced elements, so a tooltip set directly on a
                    <select> or <input> silently draws nothing. */}
                <span
                  className="block"
                  data-tip="How far this site may go on its own. Observe measures only; recommend drafts proposals for approval; autopilot deploys unattended."
                >
                  <select
                    name="mode"
                    defaultValue={data.governance.mode}
                    aria-label="Operation mode"
                    className="w-full rounded-[4px] border border-rule-strong bg-surface px-2 py-1 text-xs text-ink"
                   aria-describedby="tip-8dcda9ad6f">
                    <option value="observe">observe — measure only</option>
                    <option value="recommend">recommend — deploy with approval</option>
                    <option value="autopilot">autopilot — deploy unattended</option>
                  </select>
                </span>
                <span
                  className="block"
                  data-tip="Why you are changing the mode. Required by the API and recorded in the audit trail against your name."
                >
                  <input
                    name="reason"
                    required
                    minLength={3}
                    placeholder="Why this change"
                    aria-label="Reason for the mode change"
                    className="w-full rounded-[4px] border border-rule-strong bg-surface px-2 py-1 text-xs text-ink placeholder:text-ink-faint"
                   aria-describedby="tip-266be79a7f"/>
                </span>
                <button
                  type="submit"
                  className="w-full rounded-[4px] border border-rule-strong px-2 py-1 text-xs font-medium text-ink hover:bg-sunk"
                  data-tip="Apply the selected mode. Recorded with your reason; nothing publishes without approval unless you chose autopilot."
                 aria-describedby="tip-6f07753cce">
                  Set mode
                </button>
              </form>
            )}
          </div>
          <div className="rounded-[4px] border border-rule bg-paper p-4">
            <p className="text-xs text-ink-faint">Autopilot Governor</p>
            <p className={`text-lg font-semibold ${data.governance.autopilot_enabled ? "text-good" : "text-warn"}`}>
              {data.governance.autopilot_enabled ? "Active" : "Human Approvals Only"}
            </p>
          </div>
          <div className="rounded-[4px] border border-rule bg-paper p-4">
            <p className="text-xs text-ink-faint">Daily Change Budget</p>
            <p className="text-lg font-semibold text-ink font-mono">
              {data.governance.today_deployments_count} / {data.governance.daily_change_budget} used
            </p>
          </div>
          <div className="rounded-[4px] border border-rule bg-paper p-4">
            <p className="text-xs text-ink-faint">Approvers Required</p>
            <p className="text-lg font-semibold text-ink font-mono">
              {data.governance.required_approver_count ?? "tier default"}
            </p>
            {/*
              Raising is always allowed. Lowering stops at the tier floor for
              anything touching canonical, robots or redirect directives, so
              this cannot talk a dangerous change down to one pair of eyes. The
              count is frozen into a proposal when it is drafted, so a change
              here governs the next draft, not the ones already waiting.
            */}
            {data.site && (
              <form action={setApproverCountAction} className="mt-3 flex gap-2">
                <input type="hidden" name="site_host" value={target.host} />
                <input type="hidden" name="site_id" value={data.site.id} />
                <span
                  className="block w-full"
                  data-tip="How many different people must approve a change before it can deploy. An author can never approve their own."
                >
                  <input
                    name="required_approver_count"
                    type="number"
                    min={1}
                    max={5}
                    defaultValue={data.governance.required_approver_count ?? ""}
                    placeholder="default"
                    aria-label="Approvers required"
                    className="w-full rounded-[4px] border border-rule-strong bg-surface px-2 py-1 text-xs text-ink placeholder:text-ink-faint"
                   aria-describedby="tip-a909e65d70"/>
                </span>
                <button
                  type="submit"
                  className="rounded border border-rule-strong px-2 py-1 text-xs font-medium text-ink hover:border-rule-strong hover:text-ink"
                 data-tip="Save this approver count. It applies to proposals drafted from now on, not to ones already waiting." aria-describedby="tip-c08dbcd0f5">
                  Set
                </button>
              </form>
            )}
          </div>

          <div className="rounded-[4px] border border-rule bg-paper p-4">
            <p className="text-xs text-ink-faint">Freeze Window</p>
            <p className="text-sm font-medium text-ink-faint">
              {data.governance.freeze_window_start ? "Scheduled" : "None Active"}
            </p>
          </div>
        </section>
      )}

      {/* Site Onboarding & Crawl Actions */}
      {!data.site ? (
        <section className="rounded-[4px] border border-rule bg-paper p-6 text-center">
          <h2 className="text-lg font-semibold text-ink">No site selected</h2>
          <p className="mt-1 text-sm text-ink-faint">
            Add a site you control and prove it with a DNS record. Adding one lives in settings,
            because it needs the address and a name only you can give.
          </p>
          <Link
            href="/settings/sites"
            className="mt-4 inline-block rounded bg-surface px-4 py-2 text-xs font-semibold text-black hover:bg-sunk"
          data-tip="Add a domain you control and prove it with a DNS record." aria-describedby="tip-26b5b277ea">
            Add a site
          </Link>
        </section>
      ) : data.site.status !== "active" ? (
        <section className="rounded-[4px] border border-rule bg-paper p-6">
          <h2 className="text-base font-semibold text-ink">DNS Ownership Verification</h2>
          <p className="mt-1 text-xs text-ink-faint">Add the following DNS TXT record to verify ownership of {data.site.canonical_origin}:</p>
          {selectedChallenge && (
            <div className="mt-3 rounded border border-rule bg-surface p-3 font-mono text-xs text-ink-soft">
              <p><span className="text-ink-faint">Host:</span> {selectedChallenge.record_name}</p>
              <p><span className="text-ink-faint">Value:</span> {selectedChallenge.record_value}</p>
            </div>
          )}
          {!selectedChallenge && <p className="mt-3 text-xs text-warn">Generate a fresh site-specific TXT token before verification.</p>}
          <div className="mt-4 flex gap-3">
            <form action={verifyPortfolioDns}>
              <input type="hidden" name="site_host" value={target.host} />
              <button type="submit" className="rounded bg-good px-3 py-1.5 text-xs font-semibold text-good-ink hover:bg-good" data-tip="Check DNS now for the TXT record shown above. Safe to retry while it propagates." aria-describedby="tip-93fc91618b">
                Verify DNS Record
              </button>
            </form>
            <form action={refreshDnsChallenge}>
              <input type="hidden" name="site_host" value={target.host} />
              <button type="submit" className="rounded border border-rule-strong px-3 py-1.5 text-xs text-ink-soft hover:bg-sunk" data-tip="Issue a fresh verification token. The previous record stops working immediately." aria-describedby="tip-ad2bc41307">
                Regenerate Token
              </button>
            </form>
          </div>
          <section className="mt-5 rounded border border-rule bg-surface/50 p-4">
            <h3 className="text-sm font-semibold text-ink">DNS provider assistant</h3>
            <p className="mt-1 text-xs text-ink-faint">The manual TXT record above works with every DNS host. This optional assistant adds provider-specific support without changing the consent policy.</p>
            {data.dnsProviderConnector?.status === "active" ? (
              <form action={createDnsProviderVerification} className="mt-3">
                <input type="hidden" name="site_host" value={target.host} />
                <input type="hidden" name="provider_key" value={data.dnsProviderConnector.provider_key ?? ""} />
                <button type="submit" className="rounded bg-violet-600 px-3 py-1.5 text-xs font-semibold text-ink hover:bg-violet-500">
                  Create this TXT record with {data.dnsProviderConnector.provider_key ?? "your DNS provider"}
                </button>
              </form>
            ) : (
              <form action={connectDnsProvider} className="mt-3 grid gap-2 sm:grid-cols-2">
                <label className="text-xs text-ink-faint">Provider
                  <select name="provider_key" defaultValue="cloudflare" className="mt-1 block w-full rounded border border-rule-strong bg-paper px-2 py-1.5 text-xs text-ink">
                    <option value="cloudflare">Cloudflare (available now)</option>
                  </select>
                </label>
                <label className="text-xs text-ink-faint">Zone ID
                  <input required name="zone_id" pattern="[A-Fa-f0-9]{32}" className="mt-1 block w-full rounded border border-rule-strong bg-paper px-2 py-1.5 font-mono text-xs text-ink" />
                </label>
                <label className="text-xs text-ink-faint">Scoped API token
                  <input required name="api_token" type="password" autoComplete="off" className="mt-1 block w-full rounded border border-rule-strong bg-paper px-2 py-1.5 text-xs text-ink" />
                </label>
                <input type="hidden" name="site_host" value={target.host} />
                <p className="text-xs text-ink-faint sm:col-span-2">The selected adapter states its least-privilege scope. Connecting does not create or change DNS records; creation requires a second approval.</p>
                <button type="submit" className="justify-self-start rounded border border-violet-500 px-3 py-1.5 text-xs font-semibold text-violet-200 hover:bg-violet-950 sm:col-span-2">Connect selected provider</button>
              </form>
            )}
          </section>
        </section>
      ) : (
        <>
          {/* Quick Actions Grid */}
          <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="flex flex-col justify-between rounded-[4px] border border-rule bg-paper p-4">
              <div>
                <h3 className="text-sm font-semibold text-ink">Bounded Crawl Engine</h3>
                <p className="mt-1 text-xs text-ink-faint">
                  {data.latestCrawl ? `Last crawl: ${data.latestCrawl.status}` : "No crawl executed yet."}
                </p>
              </div>
              <form action={startFirstCrawl} className="mt-3">
                <input type="hidden" name="site_host" value={target.host} />
                <button type="submit" className="w-full rounded bg-sunk px-3 py-1.5 text-xs font-medium text-ink hover:bg-sunk"data-tip="Start a bounded crawl of this site now. Read-only: it gathers evidence and changes nothing on the site." aria-describedby="tip-e3ef53a7eb">
                  Trigger New Crawl
                </button>
              </form>
            </div>

            <div className="flex flex-col justify-between rounded-[4px] border border-rule bg-paper p-4">
              <div>
                <h3 className="text-sm font-semibold text-ink">Search Console Connector</h3>
                <p className="mt-1 text-xs text-ink-faint">
                  {data.searchPerformance ? `${data.searchPerformance.clicks} clicks / ${data.searchPerformance.impressions} impressions` : "Google Search Console ready."}
                </p>
              </div>
              <form action={connectSearchConsole} className="mt-3">
                <input type="hidden" name="site_host" value={target.host} />
                <button type="submit" className="w-full rounded bg-sunk px-3 py-1.5 text-xs font-medium text-ink hover:bg-sunk">
                  {data.searchConsoleConnector ? "Re-sync Search Console" : "Connect Search Console"}
                </button>
              </form>
            </div>

            <div className="flex flex-col justify-between rounded-[4px] border border-rule bg-paper p-4">
              <div>
                <h3 className="text-sm font-semibold text-ink">Mobile Lighthouse Lab</h3>
                <p className="mt-1 text-xs text-ink-faint">
                  {data.performanceSummary
                    ? `${data.performanceSummary.sample_count}/${data.performanceSummary.required_sample_count} samples · ${data.performanceSummary.status}`
                    : data.performanceRun?.observation
                      ? `Latest lab score: ${data.performanceRun.observation.performance_score}/100`
                      : "No lab sample yet. This is not field Core Web Vitals data."}
                </p>
              </div>
              <form action={startPerformanceRun} className="mt-3">
                <input type="hidden" name="site_host" value={target.host} />
                <input type="hidden" name="idempotency_key" value={performanceIdempotencyKey} />
                <button type="submit" className="w-full rounded bg-sunk px-3 py-1.5 text-xs font-medium text-ink hover:bg-sunk"data-tip="Run one mobile Lighthouse sample. Lab data, not real-user Core Web Vitals." aria-describedby="tip-728aeb6e5d">
                  Run Mobile Lab Sample
                </button>
              </form>
            </div>
          </section>

          <section aria-labelledby="visitor-data-heading" className="rounded-[4px] border border-rule bg-paper p-6">
            <div className="border-b border-rule pb-4">
              <h2 id="visitor-data-heading" className="text-base font-semibold text-ink">Search and visitor data</h2>
              <p className="mt-1 max-w-3xl text-xs text-ink-faint">
                Read-only, from this site&rsquo;s own Google properties
                {data.searchPerformance ? `, ${data.searchPerformance.range_start} to ${data.searchPerformance.range_end}` : ""}.
                Search Console counts searches; Analytics counts visits. They measure different things, so neither is subtracted from the other.
              </p>
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <h3 className="text-sm font-semibold text-ink">Google Search Console</h3>
                {data.searchPerformance && data.searchPerformance.rows > 0 ? (
                  <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
                    <div><dt className="text-ink-faint">Clicks</dt><dd className="font-mono text-lg text-ink">{data.searchPerformance.clicks}</dd></div>
                    <div><dt className="text-ink-faint">Impressions</dt><dd className="font-mono text-lg text-ink">{data.searchPerformance.impressions}</dd></div>
                    <div><dt className="text-ink-faint">Click-through rate</dt><dd className="font-mono text-lg text-ink">{data.searchPerformance.ctr === null ? "—" : `${(data.searchPerformance.ctr * 100).toFixed(1)}%`}</dd></div>
                    <div><dt className="text-ink-faint">Average position</dt><dd className="font-mono text-lg text-ink">{data.searchPerformance.position === null ? "—" : data.searchPerformance.position.toFixed(1)}</dd></div>
                  </dl>
                ) : (
                  <p className="mt-3 text-xs text-ink-faint">No Search Console data yet. Connect Google under Settings, then wait for the daily sync.</p>
                )}
              </div>
              <div>
                <h3 className="text-sm font-semibold text-ink">Google Analytics 4</h3>
                {data.engagement && data.engagement.rows > 0 ? (
                  <>
                    <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
                      <div><dt className="text-ink-faint">Sessions</dt><dd className="font-mono text-lg text-ink">{data.engagement.sessions}</dd></div>
                      <div><dt className="text-ink-faint">Engagement rate</dt><dd className="font-mono text-lg text-ink">{data.engagement.engagement_rate === null ? "—" : `${(data.engagement.engagement_rate * 100).toFixed(0)}%`}</dd></div>
                      <div><dt className="text-ink-faint">Page views</dt><dd className="font-mono text-lg text-ink">{data.engagement.views}</dd></div>
                      <div><dt className="text-ink-faint">Key events</dt><dd className="font-mono text-lg text-ink">{data.engagement.key_events}</dd></div>
                    </dl>
                    <table className="mt-4 w-full text-left text-xs">
                      <caption className="mb-1 text-left text-ink-faint">Top landing pages</caption>
                      <thead><tr className="text-ink-faint"><th className="py-1 font-normal">Page</th><th className="py-1 text-right font-normal">Sessions</th><th className="py-1 text-right font-normal">Engaged</th></tr></thead>
                      <tbody>
                        {data.engagement.top_landing_pages.map((page) => (
                          <tr key={page.landing_page} className="border-t border-rule">
                            <td className="max-w-0 truncate py-1 font-mono text-ink">{page.landing_page}</td>
                            <td className="py-1 text-right font-mono text-ink">{page.sessions}</td>
                            <td className="py-1 text-right font-mono text-ink">{page.engaged_sessions}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                ) : (
                  <p className="mt-3 text-xs text-ink-faint">No Analytics data yet. Connect Google under Settings; GA4 collects nothing from before its tag went live.</p>
                )}
              </div>
            </div>
          </section>

          <section aria-labelledby="advisory-heading" className="rounded-[4px] border border-rule bg-paper p-6">
            <div className="flex flex-col gap-3 border-b border-rule pb-4 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 id="advisory-heading" className="text-base font-semibold text-ink">Auto-correction Advisory Queue</h2>
                <p className="mt-1 max-w-3xl text-xs text-ink-faint">
                  Ranked candidates only. Every correction must pass evidence review, validation, and explicit human approval before a certified connector may act.
                </p>
              </div>
              <span className="w-fit rounded border border-warn-rule bg-warn-soft px-2 py-1 text-xs font-medium text-warn">
                0 automatic changes
              </span>
            </div>

            {data.opportunities.length === 0 ? (
              <div className="py-8 text-center">
                <p className="text-sm font-medium text-ink-soft">No ranked advisory candidates yet.</p>
                <p className="mt-1 text-xs text-ink-faint">Complete a bounded crawl and analysis to generate the top 20 evidence-backed opportunities.</p>
              </div>
            ) : (
              <div className="mt-4 flex flex-col gap-3">
                {data.opportunities.map((opportunity, index) => (
                  <article key={opportunity.id} className="rounded border border-rule bg-surface/40 p-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="flex gap-3">
                        <span className="flex size-7 shrink-0 items-center justify-center rounded bg-sunk text-xs font-semibold tabular-nums text-ink-soft">
                          {index + 1}
                        </span>
                        <div>
                          <h3 className="text-sm font-medium text-ink">{opportunity.title}</h3>
                          <p className="mt-1 text-xs text-ink-faint">{opportunity.type.replaceAll("_", " ")}</p>
                          {opportunity.page_url && (
                            <p className="mt-2 break-all font-mono text-xs text-sky-300">{opportunity.page_url}</p>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2 text-xs tabular-nums">
                        <span className="rounded bg-sunk px-2 py-1 text-ink-soft">Score {Math.round(opportunity.score)}</span>
                        <span className="rounded bg-sunk px-2 py-1 text-ink-soft">Confidence {Math.round(opportunity.confidence * 100)}%</span>
                        <span className="rounded border border-rule-strong px-2 py-1 uppercase text-ink-faint">{opportunity.risk} risk</span>
                        <span className="rounded border border-warn-rule bg-warn-soft px-2 py-1 text-warn">Human review required</span>
                      </div>
                      {/*
                        Drafting is not approving. This builds the change the
                        opportunity implies and submits it for review; it writes
                        nothing to the site. A repairable opportunity with no
                        button was a finding that could never become a change.
                      */}
                      <form action={draftProposalAction} className="mt-3">
                        <input type="hidden" name="site_host" value={target.host} />
                        <input type="hidden" name="opportunity_id" value={opportunity.id} />
                        <button
                          type="submit"
                          className="rounded border border-rule-strong px-3 py-1.5 text-xs font-medium text-ink hover:border-rule-strong hover:text-ink"
                         data-tip="Turn this opportunity into a reviewable proposal with an exact diff. Publishes nothing." aria-describedby="tip-6d09637632">
                          Draft proposal
                        </button>
                      </form>
                    </div>
                    <div className="mt-3 grid gap-3 border-t border-rule pt-3 sm:grid-cols-2">
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Suggested correction</p>
                        <p className="mt-1 text-xs leading-5 text-ink-soft">{advisoryFor(opportunity.title).correction}</p>
                      </div>
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Required validation</p>
                        <p className="mt-1 text-xs leading-5 text-ink-soft">{advisoryFor(opportunity.title).validation}</p>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )}

            <div className="mt-4 flex flex-col gap-3 border-t border-rule pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-xs font-medium text-ink-soft">Human quality calibration</p>
                <p className="text-xs text-ink-faint">
                  {data.calibration
                    ? `${data.calibration.summary.reviewed}/${data.calibration.target_size} reviewed${data.calibration.summary.precision === null ? "" : ` · ${Math.round(data.calibration.summary.precision * 100)}% precision`}`
                    : "Freeze a reproducible 20-item review set before trusting advisory accuracy."}
                </p>
              </div>
              {!data.calibration && data.opportunities.length > 0 && (
                <form action={createCalibrationSet}>
                  <input type="hidden" name="site_host" value={target.host} />
                  <input type="hidden" name="idempotency_key" value={randomUUID()} />
                  <button type="submit" className="rounded bg-surface px-3 py-2 text-xs font-semibold text-black hover:bg-sunk">
                    Freeze 20-item human review set
                  </button>
                </form>
              )}
            </div>
          </section>

          {/* Proposals & Review Studio */}
          <section className="rounded-[4px] border border-rule bg-paper p-6">
            <div className="flex items-center justify-between border-b border-rule pb-3">
              <div>
                <h2 className="text-base font-semibold text-ink">Proposals & Review Studio</h2>
                <p className="text-xs text-ink-faint">Auditable diffs and policy evaluations. External deployment connectors are not yet certified.</p>
              </div>
              <span className="rounded bg-surface px-2 py-1 text-xs font-mono text-ink-faint">{data.proposals.length} Proposals</span>
            </div>
            {data.proposals.length === 0 ? (
              <p className="py-6 text-center text-xs text-ink-faint">No active proposals generated yet. Run analysis to create proposals.</p>
            ) : (
              <div className="mt-4 flex flex-col gap-4">
                {data.proposals.map((prop) => (
                  <div key={prop.id} className="rounded border border-rule/80 bg-surface/50 p-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={`rounded px-2 py-0.5 text-xs font-semibold uppercase ${prop.risk === "low" ? "bg-good-soft text-good border border-good-rule" : prop.risk === "prohibited" ? "bg-stop-soft text-stop border border-stop-rule" : "bg-warn-soft text-warn border border-warn-rule"}`}>
                          {prop.risk}
                        </span>
                        <h4 className="text-sm font-medium text-ink">{prop.title}</h4>
                      </div>
                      <span className="rounded bg-sunk px-2 py-0.5 text-xs font-mono text-ink-soft">{prop.status}</span>
                    </div>
                    <p className="mt-1 text-xs text-ink-faint">{prop.rationale}</p>
                    
                    {/* Visual SERP Preview */}
                    <div className="mt-3">
                      <SerpPreview
                        title={prop.title}
                        url={`${target.origin}/${prop.target_path.replace(/^\//, "")}`}
                        description={prop.rationale}
                        isModified={true}
                      />
                    </div>

                    {/* Unified Diff View */}
                    {prop.diff_unified && (
                      <div className="mt-3 rounded border border-rule bg-black/60 p-3 font-mono text-xs text-ink-soft overflow-x-auto">
                        <pre className="text-ink-faint">{prop.diff_unified}</pre>
                      </div>
                    )}

                    <div className="mt-3 flex items-center justify-between border-t border-rule/60 pt-3">
                      <span className="text-xs text-ink-faint font-mono">Target: {prop.target_path}</span>
                      <div className="flex gap-2">
                        {/*
                          `review_required` as well as `validated`. A proposal
                          needing two approvers is created `review_required` and
                          stays there until enough approvals arrive, so gating
                          this button on `validated` alone meant a two-approver
                          change could never be approved through the app at all.
                          The API always accepted it; nothing offered it.
                        */}
                        {(prop.status === "validated" || prop.status === "review_required") && (
                          <>
                            <form action={approveProposalAction}>
                              <input type="hidden" name="site_host" value={target.host} />
                              <input type="hidden" name="proposal_id" value={prop.id} />
                              <button type="submit" className="rounded bg-good px-2.5 py-1 text-xs font-semibold text-good-ink hover:bg-good" data-tip="Record your approval. You cannot approve a change you authored yourself." aria-describedby="tip-ec0ccb8c77">
                                Approve
                              </button>
                            </form>
                            {/*
                              An author cannot approve their own change and,
                              until now, could not clear it either — a bad draft
                              stayed in the queue for ever. Withdrawing ends the
                              proposal and can never put anything on a site.
                            */}
                            <form action={withdrawProposalAction}>
                              <input type="hidden" name="site_host" value={target.host} />
                              <input type="hidden" name="proposal_id" value={prop.id} />
                              <button type="submit" className="rounded border border-rule-strong px-2.5 py-1 text-xs font-medium text-ink-soft hover:border-rule-strong hover:text-ink" data-tip="Take this proposal off the table. It stops counting toward approvals and cannot deploy." aria-describedby="tip-5be8fcba56">
                                Withdraw
                              </button>
                            </form>
                          </>
                        )}
                        {/*
                          An approved proposal used to render the words
                          "Connector certification required" and nothing else.
                          The endpoint existed, the action existed, and no
                          button in the app called it -- so a change could be
                          drafted, validated and approved here and then had to
                          be deployed by hand with a bearer token. The sentence
                          was also untrue: the repository connector is active
                          and has carried 48 deployments for another site.

                          Deploying opens a pull request. It does not merge:
                          the adapter has never had merge authority, so the
                          change reaches the live site only when a person
                          merges it.
                        */}
                        {prop.status === "approved" && (
                          <form action={deployProposalAction}>
                            <input type="hidden" name="site_host" value={target.host} />
                            <input type="hidden" name="proposal_id" value={prop.id} />
                            {/*
                              Keyed on the proposal, so a double click or a
                              retry after a timeout is the same intent rather
                              than a second pull request.
                            */}
                            <input type="hidden" name="idempotency_key" value={`deploy-${prop.id}`} />
                            <button type="submit" className="rounded bg-sky-600 px-2.5 py-1 text-xs font-semibold text-ink hover:bg-sky-500" data-tip="Open the pull request that carries this change on your repository. A human still merges it." aria-describedby="tip-8840e44e3f">
                              Deploy
                            </button>
                          </form>
                        )}
                        {/*
                          Likewise: "External rollback not configured" stood
                          where the control belonged. Rollback is configured --
                          it opens a revert pull request, and the reconciler
                          watches whether anybody merges it. Requesting one is
                          not undoing anything, which is why the label says
                          what it does.
                        */}
                        {prop.status === "deployed" && (
                          <form action={rollbackProposalAction}>
                            <input type="hidden" name="site_host" value={target.host} />
                            <input type="hidden" name="proposal_id" value={prop.id} />
                            <button type="submit" className="rounded border border-stop-rule px-2.5 py-1 text-xs font-medium text-stop hover:border-stop-rule hover:text-stop" data-tip="Open a revert pull request. The change is only undone once someone merges it." aria-describedby="tip-638cd2f7c3">
                              Request revert
                            </button>
                          </form>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* 28-Day Outcome Tracking Dashboard */}
          <section className="rounded-[4px] border border-rule bg-paper p-6">
            <div className="flex items-center justify-between border-b border-rule pb-3">
              <div>
                <h2 className="text-base font-semibold text-ink">28-Day Outcome Tracking</h2>
                <p className="text-xs text-ink-faint">Before-and-after association after independent deployment verification; this does not establish causation.</p>
              </div>
              <span className="rounded bg-surface px-2 py-1 text-xs font-mono text-ink-faint">{data.measurements.length} Series</span>
            </div>
            {data.measurements.length === 0 ? (
              <p className="py-6 text-center text-xs text-ink-faint">No measurement series recorded yet. Deployed proposals will track here over 28-day windows.</p>
            ) : (
              <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                {data.measurements.map((m) => (
                  <div key={m.id} className="rounded border border-rule bg-surface/40 p-4">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-ink-faint">Evidence Completeness</span>
                      <span className="rounded bg-good-soft border border-good-rule px-2 py-0.5 text-xs font-mono text-good">
                        {Math.round(m.confidence_score * 100)}%
                      </span>
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                      <div className="rounded bg-surface p-2">
                        <p className="text-ink-faint">Clicks Delta</p>
                        <p className="text-base font-semibold font-mono text-ink">
                          {typeof m.delta_metrics?.clicks_delta === "number"
                            ? m.delta_metrics.clicks_delta >= 0
                              ? `+${m.delta_metrics.clicks_delta}`
                              : m.delta_metrics.clicks_delta
                            : "N/A"}
                        </p>
                      </div>
                      <div className="rounded bg-surface p-2">
                        <p className="text-ink-faint">Position Delta</p>
                        <p className="text-base font-semibold font-mono text-ink">
                          {typeof m.delta_metrics?.position_delta === "number"
                            ? m.delta_metrics.position_delta >= 0
                              ? `+${m.delta_metrics.position_delta} ranks`
                              : `${m.delta_metrics.position_delta} ranks`
                            : "N/A"}
                        </p>
                      </div>
                    </div>
                    {m.annotations?.length > 0 && (
                      <p className="mt-2 text-xs italic text-ink-faint">{m.annotations[0]}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}
      </div>
    </main>
  );
}
