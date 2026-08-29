import {cookies} from "next/headers";
import type {Metadata} from "next";
import {randomUUID} from "node:crypto";
import Link from "next/link";

import {apiJson} from "@/lib/server-api";

import {
  approveProposalAction,
  connectDnsProvider,
  connectSearchConsole,
  createDnsProviderVerification,
  createCalibrationSet,
  onboardPortfolioSite,
  refreshDnsChallenge,
  runPolicySimulationAction,
  startFirstCrawl,
  startPerformanceRun,
  toggleEmergencyFreezeAction,
  verifyPortfolioDns,
} from "./actions";
import {advisoryFor} from "./advisory.mjs";
import {SerpPreview} from "./components/serp-preview";
import {challengeCookie, type CalibrationRun, type Crawl, type Site} from "./model";
import {pilotPath, portfolioSites, resolvePortfolioSite} from "./portfolio.mjs";

type PortfolioSite = (typeof portfolioSites)[number];

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
  target: PortfolioSite;
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
  performanceRun?: PerformanceRun;
  performanceSummary?: PerformanceSummary;
}> {
  const target = resolvePortfolioSite(requestedHost);
  try {
    const sites = await apiJson<{data: Site[]}>("/v1/sites");
    const site = sites.data.find((item) => item.normalized_host === target.host);
    if (!site) return {target, sites: sites.data, opportunities: [], proposals: [], measurements: []};
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
      performanceRun,
      performanceSummary,
    };
  } catch {
    return {target, sites: [], opportunities: [], proposals: [], measurements: []};
  }
}

export default async function PilotPage({searchParams}: PageProps) {
  const {error, governance: govUpdated, simulation, proposal: propMsg, site: requestedHost} = await searchParams;
  const data = await loadPilot(requestedHost);
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
    <main className="min-h-dvh bg-zinc-950 text-zinc-100">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 p-6">
      {/* Emergency Freeze Banner */}
      {data.governance?.emergency_freeze && (
        <div className="flex items-center justify-between rounded-lg border border-red-500 bg-red-950/40 p-4 text-red-200">
          <div className="flex items-center gap-3">
            <span aria-hidden="true" className="text-xl">!</span>
            <div>
              <p className="font-semibold">EMERGENCY KILL-SWITCH ACTIVE</p>
              <p className="text-xs text-red-300">Automated deployment is blocked site-wide.</p>
            </div>
          </div>
          <p className="max-w-56 text-right text-xs text-red-300">An owner must complete incident review before lifting this freeze through the governed API.</p>
        </div>
      )}

      {/* Header */}
      <header className="flex flex-col gap-1 border-b border-zinc-800 pb-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight text-white">SEO Autopilot Operations</h1>
            <span className="rounded-full bg-emerald-950 border border-emerald-800 px-2.5 py-0.5 text-xs font-medium text-emerald-400">
              Internal Alpha
            </span>
          </div>
          {data.site && data.governance && (
            <div className="flex items-center gap-2">
              <form action={runPolicySimulationAction}>
                <input type="hidden" name="site_host" value={data.target.host} />
                <button type="submit" className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800">
                  Run Policy Simulation
                </button>
              </form>
              {!data.governance.emergency_freeze && (
                <form action={toggleEmergencyFreezeAction}>
                  <input type="hidden" name="site_host" value={data.target.host} />
                  <input type="hidden" name="current_freeze" value="false" />
                  <button type="submit" className="rounded border border-red-800 bg-red-950/60 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-900">
                    Emergency Freeze
                  </button>
                </form>
              )}
            </div>
          )}
        </div>
        <p className="text-sm text-zinc-400">
          Auditable, multi-tenant SEO evidence and governance control plane for <span className="font-medium text-zinc-200">{data.target.host}</span>.
        </p>
      </header>

      <section aria-labelledby="portfolio-heading" className="rounded-lg border border-zinc-800 bg-zinc-950 p-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 id="portfolio-heading" className="text-sm font-semibold text-white">Owned-site evidence lab</h2>
            <p className="text-xs text-zinc-400">Compare the same bounded workflow across three owned sites. Selection never grants permission to publish changes.</p>
          </div>
          <span className="mt-2 w-fit rounded border border-amber-800 bg-amber-950/50 px-2 py-1 text-xs font-medium text-amber-300 sm:mt-0">
            Human consent required
          </span>
        </div>
        <nav aria-label="Portfolio sites" className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {portfolioSites.map((portfolioSite) => {
            const existing = data.sites.find((item) => item.normalized_host === portfolioSite.host);
            const selected = portfolioSite.host === data.target.host;
            return (
              <Link
                key={portfolioSite.host}
                href={pilotPath(portfolioSite.host)}
                aria-current={selected ? "page" : undefined}
                className={`rounded border p-3 outline-none focus-visible:ring-2 focus-visible:ring-white ${selected ? "border-white bg-zinc-900" : "border-zinc-800 bg-zinc-950 hover:bg-zinc-900"}`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-white">{portfolioSite.name}</span>
                  <span className={`rounded px-2 py-0.5 text-xs ${existing?.status === "active" ? "bg-emerald-950 text-emerald-300" : "bg-zinc-800 text-zinc-400"}`}>
                    {existing?.status ?? "Not onboarded"}
                  </span>
                </span>
                <span className="mt-1 block text-xs text-zinc-500">{portfolioSite.host}</span>
              </Link>
            );
          })}
        </nav>
      </section>

      {/* Notifications */}
      {error && (
        <div role="alert" className="rounded border border-red-800 bg-red-950/50 p-3 text-xs text-red-300">
          {errorMessages[error] ?? `Error: ${error}`}
        </div>
      )}
      {govUpdated && <div role="status" aria-live="polite" className="rounded border border-emerald-800 bg-emerald-950/40 p-3 text-xs text-emerald-300">Governance settings updated.</div>}
      {simulation && <div role="status" aria-live="polite" className="rounded border border-sky-800 bg-sky-950/40 p-3 text-xs text-sky-300">Policy simulation completed successfully.</div>}
      {propMsg && <div role="status" aria-live="polite" className="rounded border border-emerald-800 bg-emerald-950/40 p-3 text-xs text-emerald-300">Proposal status transition applied: {propMsg}.</div>}

      {/* Governance & Autopilot Status Bar */}
      {data.governance && (
        <section className="grid grid-cols-1 gap-4 sm:grid-cols-4">
          <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-4">
            <p className="text-xs text-zinc-500">Operation Mode</p>
            <p className="text-lg font-semibold uppercase text-zinc-200">{data.governance.mode}</p>
          </div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-4">
            <p className="text-xs text-zinc-500">Autopilot Governor</p>
            <p className={`text-lg font-semibold ${data.governance.autopilot_enabled ? "text-emerald-400" : "text-amber-400"}`}>
              {data.governance.autopilot_enabled ? "Active" : "Human Approvals Only"}
            </p>
          </div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-4">
            <p className="text-xs text-zinc-500">Daily Change Budget</p>
            <p className="text-lg font-semibold text-zinc-200 font-mono">
              {data.governance.today_deployments_count} / {data.governance.daily_change_budget} used
            </p>
          </div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-4">
            <p className="text-xs text-zinc-500">Freeze Window</p>
            <p className="text-sm font-medium text-zinc-400">
              {data.governance.freeze_window_start ? "Scheduled" : "None Active"}
            </p>
          </div>
        </section>
      )}

      {/* Site Onboarding & Crawl Actions */}
      {!data.site ? (
        <section className="rounded-lg border border-zinc-800 bg-zinc-950 p-6 text-center">
          <h2 className="text-lg font-semibold text-white">Initialize Target Site</h2>
          <p className="mt-1 text-sm text-zinc-400">Onboard {data.target.host} in Observe mode, then prove ownership with a DNS TXT record.</p>
          <form action={onboardPortfolioSite} className="mt-4">
            <input type="hidden" name="site_host" value={data.target.host} />
            <button type="submit" className="rounded bg-white px-4 py-2 text-xs font-semibold text-black hover:bg-zinc-200">
              Onboard {data.target.name}
            </button>
          </form>
        </section>
      ) : data.site.status !== "active" ? (
        <section className="rounded-lg border border-zinc-800 bg-zinc-950 p-6">
          <h2 className="text-base font-semibold text-white">DNS Ownership Verification</h2>
          <p className="mt-1 text-xs text-zinc-400">Add the following DNS TXT record to verify ownership of {data.site.canonical_origin}:</p>
          {selectedChallenge && (
            <div className="mt-3 rounded border border-zinc-800 bg-zinc-900 p-3 font-mono text-xs text-zinc-300">
              <p><span className="text-zinc-500">Host:</span> {selectedChallenge.record_name}</p>
              <p><span className="text-zinc-500">Value:</span> {selectedChallenge.record_value}</p>
            </div>
          )}
          {!selectedChallenge && <p className="mt-3 text-xs text-amber-300">Generate a fresh site-specific TXT token before verification.</p>}
          <div className="mt-4 flex gap-3">
            <form action={verifyPortfolioDns}>
              <input type="hidden" name="site_host" value={data.target.host} />
              <button type="submit" className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500">
                Verify DNS Record
              </button>
            </form>
            <form action={refreshDnsChallenge}>
              <input type="hidden" name="site_host" value={data.target.host} />
              <button type="submit" className="rounded border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800">
                Regenerate Token
              </button>
            </form>
          </div>
          <section className="mt-5 rounded border border-zinc-800 bg-zinc-900/50 p-4">
            <h3 className="text-sm font-semibold text-zinc-100">DNS provider assistant</h3>
            <p className="mt-1 text-xs text-zinc-400">The manual TXT record above works with every DNS host. This optional assistant adds provider-specific support without changing the consent policy.</p>
            {data.dnsProviderConnector?.status === "active" ? (
              <form action={createDnsProviderVerification} className="mt-3">
                <input type="hidden" name="site_host" value={data.target.host} />
                <input type="hidden" name="provider_key" value={data.dnsProviderConnector.provider_key ?? ""} />
                <button type="submit" className="rounded bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-500">
                  Create this TXT record with {data.dnsProviderConnector.provider_key ?? "your DNS provider"}
                </button>
              </form>
            ) : (
              <form action={connectDnsProvider} className="mt-3 grid gap-2 sm:grid-cols-2">
                <label className="text-xs text-zinc-400">Provider
                  <select name="provider_key" defaultValue="cloudflare" className="mt-1 block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs text-zinc-100">
                    <option value="cloudflare">Cloudflare (available now)</option>
                  </select>
                </label>
                <label className="text-xs text-zinc-400">Zone ID
                  <input required name="zone_id" pattern="[A-Fa-f0-9]{32}" className="mt-1 block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 font-mono text-xs text-zinc-100" />
                </label>
                <label className="text-xs text-zinc-400">Scoped API token
                  <input required name="api_token" type="password" autoComplete="off" className="mt-1 block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs text-zinc-100" />
                </label>
                <input type="hidden" name="site_host" value={data.target.host} />
                <p className="text-xs text-zinc-500 sm:col-span-2">The selected adapter states its least-privilege scope. Connecting does not create or change DNS records; creation requires a second approval.</p>
                <button type="submit" className="justify-self-start rounded border border-violet-500 px-3 py-1.5 text-xs font-semibold text-violet-200 hover:bg-violet-950 sm:col-span-2">Connect selected provider</button>
              </form>
            )}
          </section>
        </section>
      ) : (
        <>
          {/* Quick Actions Grid */}
          <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="flex flex-col justify-between rounded-lg border border-zinc-800 bg-zinc-950 p-4">
              <div>
                <h3 className="text-sm font-semibold text-white">Bounded Crawl Engine</h3>
                <p className="mt-1 text-xs text-zinc-400">
                  {data.latestCrawl ? `Last crawl: ${data.latestCrawl.status}` : "No crawl executed yet."}
                </p>
              </div>
              <form action={startFirstCrawl} className="mt-3">
                <input type="hidden" name="site_host" value={data.target.host} />
                <button type="submit" className="w-full rounded bg-zinc-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-700">
                  Trigger New Crawl
                </button>
              </form>
            </div>

            <div className="flex flex-col justify-between rounded-lg border border-zinc-800 bg-zinc-950 p-4">
              <div>
                <h3 className="text-sm font-semibold text-white">Search Console Connector</h3>
                <p className="mt-1 text-xs text-zinc-400">
                  {data.searchPerformance ? `${data.searchPerformance.clicks} clicks / ${data.searchPerformance.impressions} impressions` : "Google Search Console ready."}
                </p>
              </div>
              <form action={connectSearchConsole} className="mt-3">
                <input type="hidden" name="site_host" value={data.target.host} />
                <button type="submit" className="w-full rounded bg-zinc-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-700">
                  {data.searchConsoleConnector ? "Re-sync Search Console" : "Connect Search Console"}
                </button>
              </form>
            </div>

            <div className="flex flex-col justify-between rounded-lg border border-zinc-800 bg-zinc-950 p-4">
              <div>
                <h3 className="text-sm font-semibold text-white">Mobile Lighthouse Lab</h3>
                <p className="mt-1 text-xs text-zinc-400">
                  {data.performanceSummary
                    ? `${data.performanceSummary.sample_count}/${data.performanceSummary.required_sample_count} samples · ${data.performanceSummary.status}`
                    : data.performanceRun?.observation
                      ? `Latest lab score: ${data.performanceRun.observation.performance_score}/100`
                      : "No lab sample yet. This is not field Core Web Vitals data."}
                </p>
              </div>
              <form action={startPerformanceRun} className="mt-3">
                <input type="hidden" name="site_host" value={data.target.host} />
                <input type="hidden" name="idempotency_key" value={performanceIdempotencyKey} />
                <button type="submit" className="w-full rounded bg-zinc-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-700">
                  Run Mobile Lab Sample
                </button>
              </form>
            </div>
          </section>

          <section aria-labelledby="advisory-heading" className="rounded-lg border border-zinc-800 bg-zinc-950 p-6">
            <div className="flex flex-col gap-3 border-b border-zinc-800 pb-4 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 id="advisory-heading" className="text-base font-semibold text-white">Auto-correction Advisory Queue</h2>
                <p className="mt-1 max-w-3xl text-xs text-zinc-400">
                  Ranked candidates only. Every correction must pass evidence review, validation, and explicit human approval before a certified connector may act.
                </p>
              </div>
              <span className="w-fit rounded border border-amber-800 bg-amber-950/50 px-2 py-1 text-xs font-medium text-amber-300">
                0 automatic changes
              </span>
            </div>

            {data.opportunities.length === 0 ? (
              <div className="py-8 text-center">
                <p className="text-sm font-medium text-zinc-300">No ranked advisory candidates yet.</p>
                <p className="mt-1 text-xs text-zinc-500">Complete a bounded crawl and analysis to generate the top 20 evidence-backed opportunities.</p>
              </div>
            ) : (
              <div className="mt-4 flex flex-col gap-3">
                {data.opportunities.map((opportunity, index) => (
                  <article key={opportunity.id} className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="flex gap-3">
                        <span className="flex size-7 shrink-0 items-center justify-center rounded bg-zinc-800 text-xs font-semibold tabular-nums text-zinc-300">
                          {index + 1}
                        </span>
                        <div>
                          <h3 className="text-sm font-medium text-white">{opportunity.title}</h3>
                          <p className="mt-1 text-xs text-zinc-500">{opportunity.type.replaceAll("_", " ")}</p>
                          {opportunity.page_url && (
                            <p className="mt-2 break-all font-mono text-xs text-sky-300">{opportunity.page_url}</p>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2 text-xs tabular-nums">
                        <span className="rounded bg-zinc-800 px-2 py-1 text-zinc-300">Score {Math.round(opportunity.score)}</span>
                        <span className="rounded bg-zinc-800 px-2 py-1 text-zinc-300">Confidence {Math.round(opportunity.confidence * 100)}%</span>
                        <span className="rounded border border-zinc-700 px-2 py-1 uppercase text-zinc-400">{opportunity.risk} risk</span>
                        <span className="rounded border border-amber-800 bg-amber-950/40 px-2 py-1 text-amber-300">Human review required</span>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-3 border-t border-zinc-800 pt-3 sm:grid-cols-2">
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Suggested correction</p>
                        <p className="mt-1 text-xs leading-5 text-zinc-300">{advisoryFor(opportunity.title).correction}</p>
                      </div>
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Required validation</p>
                        <p className="mt-1 text-xs leading-5 text-zinc-300">{advisoryFor(opportunity.title).validation}</p>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )}

            <div className="mt-4 flex flex-col gap-3 border-t border-zinc-800 pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-xs font-medium text-zinc-300">Human quality calibration</p>
                <p className="text-xs text-zinc-500">
                  {data.calibration
                    ? `${data.calibration.summary.reviewed}/${data.calibration.target_size} reviewed${data.calibration.summary.precision === null ? "" : ` · ${Math.round(data.calibration.summary.precision * 100)}% precision`}`
                    : "Freeze a reproducible 20-item review set before trusting advisory accuracy."}
                </p>
              </div>
              {!data.calibration && data.opportunities.length > 0 && (
                <form action={createCalibrationSet}>
                  <input type="hidden" name="site_host" value={data.target.host} />
                  <input type="hidden" name="idempotency_key" value={randomUUID()} />
                  <button type="submit" className="rounded bg-white px-3 py-2 text-xs font-semibold text-black hover:bg-zinc-200">
                    Freeze 20-item human review set
                  </button>
                </form>
              )}
            </div>
          </section>

          {/* Proposals & Review Studio */}
          <section className="rounded-lg border border-zinc-800 bg-zinc-950 p-6">
            <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
              <div>
                <h2 className="text-base font-semibold text-white">Proposals & Review Studio</h2>
                <p className="text-xs text-zinc-400">Auditable diffs and policy evaluations. External deployment connectors are not yet certified.</p>
              </div>
              <span className="rounded bg-zinc-900 px-2 py-1 text-xs font-mono text-zinc-400">{data.proposals.length} Proposals</span>
            </div>
            {data.proposals.length === 0 ? (
              <p className="py-6 text-center text-xs text-zinc-500">No active proposals generated yet. Run analysis to create proposals.</p>
            ) : (
              <div className="mt-4 flex flex-col gap-4">
                {data.proposals.map((prop) => (
                  <div key={prop.id} className="rounded border border-zinc-800/80 bg-zinc-900/50 p-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={`rounded px-2 py-0.5 text-xs font-semibold uppercase ${prop.risk === "low" ? "bg-emerald-950 text-emerald-400 border border-emerald-800" : prop.risk === "prohibited" ? "bg-red-950 text-red-400 border border-red-800" : "bg-amber-950 text-amber-400 border border-amber-800"}`}>
                          {prop.risk}
                        </span>
                        <h4 className="text-sm font-medium text-white">{prop.title}</h4>
                      </div>
                      <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs font-mono text-zinc-300">{prop.status}</span>
                    </div>
                    <p className="mt-1 text-xs text-zinc-400">{prop.rationale}</p>
                    
                    {/* Visual SERP Preview */}
                    <div className="mt-3">
                      <SerpPreview
                        title={prop.title}
                        url={`${data.target.origin}/${prop.target_path.replace(/^\//, "")}`}
                        description={prop.rationale}
                        isModified={true}
                      />
                    </div>

                    {/* Unified Diff View */}
                    {prop.diff_unified && (
                      <div className="mt-3 rounded border border-zinc-800 bg-black/60 p-3 font-mono text-xs text-zinc-300 overflow-x-auto">
                        <pre className="text-zinc-400">{prop.diff_unified}</pre>
                      </div>
                    )}

                    <div className="mt-3 flex items-center justify-between border-t border-zinc-800/60 pt-3">
                      <span className="text-xs text-zinc-500 font-mono">Target: {prop.target_path}</span>
                      <div className="flex gap-2">
                        {prop.status === "validated" && (
                          <form action={approveProposalAction}>
                            <input type="hidden" name="site_host" value={data.target.host} />
                            <input type="hidden" name="proposal_id" value={prop.id} />
                            <button type="submit" className="rounded bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-emerald-500">
                              Approve
                            </button>
                          </form>
                        )}
                        {prop.status === "approved" && (
                          <span className="rounded border border-amber-800 bg-amber-950 px-2.5 py-1 text-xs font-medium text-amber-300">
                            Connector certification required
                          </span>
                        )}
                        {prop.status === "deployed" && (
                          <span className="rounded border border-red-800 bg-red-950 px-2.5 py-1 text-xs font-medium text-red-300">
                            External rollback not configured
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* 28-Day Outcome Tracking Dashboard */}
          <section className="rounded-lg border border-zinc-800 bg-zinc-950 p-6">
            <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
              <div>
                <h2 className="text-base font-semibold text-white">28-Day Outcome Tracking</h2>
                <p className="text-xs text-zinc-400">Before-and-after association after independent deployment verification; this does not establish causation.</p>
              </div>
              <span className="rounded bg-zinc-900 px-2 py-1 text-xs font-mono text-zinc-400">{data.measurements.length} Series</span>
            </div>
            {data.measurements.length === 0 ? (
              <p className="py-6 text-center text-xs text-zinc-500">No measurement series recorded yet. Deployed proposals will track here over 28-day windows.</p>
            ) : (
              <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                {data.measurements.map((m) => (
                  <div key={m.id} className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-zinc-400">Evidence Completeness</span>
                      <span className="rounded bg-emerald-950 border border-emerald-800 px-2 py-0.5 text-xs font-mono text-emerald-300">
                        {Math.round(m.confidence_score * 100)}%
                      </span>
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                      <div className="rounded bg-zinc-900 p-2">
                        <p className="text-zinc-500">Clicks Delta</p>
                        <p className="text-base font-semibold font-mono text-white">
                          {typeof m.delta_metrics?.clicks_delta === "number"
                            ? m.delta_metrics.clicks_delta >= 0
                              ? `+${m.delta_metrics.clicks_delta}`
                              : m.delta_metrics.clicks_delta
                            : "N/A"}
                        </p>
                      </div>
                      <div className="rounded bg-zinc-900 p-2">
                        <p className="text-zinc-500">Position Delta</p>
                        <p className="text-base font-semibold font-mono text-white">
                          {typeof m.delta_metrics?.position_delta === "number"
                            ? m.delta_metrics.position_delta >= 0
                              ? `+${m.delta_metrics.position_delta} ranks`
                              : `${m.delta_metrics.position_delta} ranks`
                            : "N/A"}
                        </p>
                      </div>
                    </div>
                    {m.annotations?.length > 0 && (
                      <p className="mt-2 text-xs italic text-zinc-500">{m.annotations[0]}</p>
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
