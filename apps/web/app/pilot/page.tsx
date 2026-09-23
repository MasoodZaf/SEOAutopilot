import {cookies} from "next/headers";
import type {Metadata} from "next";
import {randomUUID} from "node:crypto";
import Link from "next/link";

import {redirect} from "next/navigation";

import {Badge, button, Copyable, Field, input, inputMono, Meter, Metric, Note, Panel, type Tone} from "@/app/components/ui";
import {cn} from "@/lib/cn";
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
import {CrawlPanel} from "./crawl-panel";
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
  // Where the change is published. For a new post, not its repository path.
  page_url?: string | null;
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
  notRechecked?: number;
  proposals: Proposal[];
  governance?: GovernanceStatus;
  measurements: Measurement[];
  calibration?: CalibrationRun;
  latestCrawl?: Crawl;
  crawls?: Crawl[];
  renderedAt?: string;
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
    // The API ranks only what the latest analyzed crawl confirmed, and counts
    // the open issues it did not re-check (pages a page-limited crawl did not
    // reach). Those are left out of the queue, not closed.
    const oppResult: {data: Opportunity[]; meta?: {not_rechecked?: number}} = site.status === "active"
      ? await apiJson<{data: Opportunity[]; meta?: {not_rechecked?: number}}>(`/v1/sites/${site.id}/opportunities?limit=20&status=open`)
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

    let searchPerformance: SearchPerformance | undefined;
    let engagement: Engagement | undefined;
    let performanceRun: PerformanceRun | undefined;
    let performanceSummary: PerformanceSummary | undefined;
    const crawls = (await apiJson<{data: Crawl[]}>(`/v1/sites/${site.id}/crawls?limit=5`)).data;
    const latestCrawl: Crawl | undefined = crawls[0];
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
      notRechecked: oppResult.meta?.not_rechecked,
      proposals,
      governance,
      measurements,
      calibration,
      latestCrawl,
      crawls,
      renderedAt: new Date().toISOString(),
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

/** A numbered section heading: the page reads as a sequence, top to bottom. */
function SectionHead({
  index,
  title,
  id,
  description,
  aside,
}: {
  index: string;
  title: string;
  id: string;
  description?: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <p className="eyebrow flex items-center gap-3">
          <span className="text-accent">{index}</span>
          <span aria-hidden="true" className="h-px w-8 bg-rule-strong" />
        </p>
        <h2 id={id} className="mt-3 font-display text-[26px] leading-tight font-light tracking-tight text-balance text-ink">
          {title}
        </h2>
        {description ? (
          <p className="mt-2 max-w-2xl text-[13px] leading-6 text-pretty text-ink-soft">{description}</p>
        ) : null}
      </div>
      {aside ? <div className="shrink-0">{aside}</div> : null}
    </div>
  );
}

const riskTone = (risk: string): Tone =>
  risk === "low" ? "good" : risk === "prohibited" ? "stop" : "warn";

const smallButton = {
  primary:
    "inline-flex items-center justify-center rounded-full bg-signal px-3.5 py-1.5 text-[12px] font-semibold text-signal-ink transition-colors hover:bg-signal-hover",
  secondary:
    "inline-flex items-center justify-center rounded-full border border-rule-strong px-3.5 py-1.5 text-[12px] font-medium text-ink transition-colors hover:border-ink-faint hover:bg-sunk",
  caution:
    "inline-flex items-center justify-center rounded-full border border-stop-rule px-3.5 py-1.5 text-[12px] font-medium text-stop transition-colors hover:bg-stop-soft",
} as const;

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
  const gov = data.governance;
  const crawlActive = Boolean(data.latestCrawl && ["queued", "running"].includes(data.latestCrawl.status));

  return (
    <main className="bg-paper text-ink">
      {gov?.emergency_freeze && (
        <div role="alert" className="border-b border-stop-rule bg-stop-soft">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-3">
            <p className="flex items-center gap-3 text-[13px] text-ink">
              <Badge tone="stop">Kill switch active</Badge>
              Automated deployment is blocked site-wide.
            </p>
            <p className="max-w-md text-[12px] text-pretty text-stop">
              An owner must complete incident review before lifting this freeze through the governed API.
            </p>
          </div>
        </div>
      )}

      {/* The masthead in the layout carries the product name, so this header
          names the thing the page is actually about: the site being operated
          on. The host is the one fact that changes what every control below
          does, so it is the largest thing on the page. */}
      <header className="dot-field border-b border-rule">
        <div className="mx-auto max-w-6xl px-6 pt-12 pb-8">
          <div className="flex flex-col gap-8 lg:flex-row lg:items-end lg:justify-between">
            <div className="min-w-0">
              <p className="eyebrow">Operating on</p>
              <h1 className="mt-3 font-display text-[40px] leading-none font-light tracking-tight break-words text-ink sm:text-[60px]">
                {target.host || target.name}
              </h1>
              <div className="mt-5 flex flex-wrap items-center gap-2">
                {data.site ? (
                  <Badge tone={data.site.status === "active" ? "good" : "warn"}>
                    {data.site.status.replaceAll("_", " ")}
                  </Badge>
                ) : null}
                {gov ? <Badge tone="accent">{gov.mode}</Badge> : null}
                <Badge tone="warn">Human consent required</Badge>
              </div>
            </div>
            {data.site && gov && (
              <div className="flex flex-wrap items-center gap-2">
                {/* Navigation, not a status. */}
                <Link
                  href={`/pilot/workspace?site=${target.host}`}
                  className={button.primary}
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
                {!gov.emergency_freeze && (
                  <form action={toggleEmergencyFreezeAction}>
                    <input type="hidden" name="site_host" value={target.host} />
                    <input type="hidden" name="current_freeze" value="false" />
                    <button
                      type="submit"
                      className={button.caution}
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

          {/* Selection never grants permission to publish changes. */}
          <nav aria-label="Sites in this workspace" className="mt-10 flex flex-wrap gap-2">
            {data.sites.map((item) => {
              const selected = item.normalized_host === target.host;
              return (
                <Link
                  key={item.id}
                  href={pilotPath(item.normalized_host)}
                  aria-current={selected ? "page" : undefined}
                  className={cn(
                    "group inline-flex items-center gap-2.5 rounded-full border py-1.5 pr-4 pl-3 text-[13px] transition-colors",
                    selected
                      ? "border-ink bg-ink text-ink-inverse"
                      : "border-rule-strong bg-surface text-ink-soft hover:border-ink-faint hover:text-ink",
                  )}
                >
                  <span
                    aria-hidden="true"
                    className={cn("size-1.5 rounded-full", item.status === "active" ? "bg-good" : "bg-ink-faint")}
                  />
                  <span className="font-medium">{item.name}</span>
                  <span className={cn("font-mono text-[11px]", selected ? "text-ink-inverse" : "text-ink-faint")}>
                    {item.normalized_host}
                  </span>
                  <span className="sr-only">{item.status}</span>
                </Link>
              );
            })}
            <Link
              href="/settings/sites"
              className="inline-flex items-center gap-2 rounded-full border border-dashed border-rule-strong px-4 py-1.5 text-[13px] text-ink-soft transition-colors hover:border-ink-faint hover:text-ink"
            >
              <span aria-hidden="true">+</span> Add a site
            </Link>
          </nav>
        </div>
      </header>

      <div className="mx-auto flex max-w-6xl flex-col gap-14 px-6 py-10">
        {(error || govUpdated || simulation || propMsg) && (
          <div className="flex flex-col gap-2">
            {error && (
              <Note tone="stop" role="alert">
                {errorMessages[error] ?? `Error: ${error}`}
              </Note>
            )}
            {govUpdated && <Note tone="good" role="status">Governance settings updated.</Note>}
            {simulation && <Note tone="accent" role="status">Policy simulation completed successfully.</Note>}
            {propMsg && <Note tone="good" role="status">Proposal status transition applied: {propMsg}.</Note>}
          </div>
        )}

        {/* Governance, as one strip of readings. The controls that change it sit
            behind a disclosure: they are used rarely and deliberately, and five
            always-open forms made the readings hard to find. */}
        {gov && (
          <section aria-labelledby="governance-heading">
            <h2 id="governance-heading" className="sr-only">Governance</h2>
            <Panel as="div" className="overflow-hidden">
              <dl className="grid grid-cols-2 divide-rule sm:grid-cols-3 lg:grid-cols-5 lg:divide-x">
                <Metric
                  className="border-b border-rule p-6 lg:border-b-0"
                  label="Operation mode"
                  value={<span className="uppercase">{gov.mode}</span>}
                />
                <Metric
                  className="border-b border-rule p-6 lg:border-b-0"
                  label="Autopilot governor"
                  value={<span className={gov.autopilot_enabled ? "text-good" : "text-warn"}>{gov.autopilot_enabled ? "Active" : "Human"}</span>}
                  hint={gov.autopilot_enabled ? undefined : "Approvals only"}
                />
                <div className="border-b border-rule p-6 lg:border-b-0">
                  <Metric
                    label="Daily change budget"
                    value={gov.today_deployments_count}
                    unit={`/ ${gov.daily_change_budget} used`}
                  />
                  <Meter
                    className="mt-4"
                    label="Daily change budget used"
                    value={gov.daily_change_budget ? gov.today_deployments_count / gov.daily_change_budget : 0}
                    tone={gov.today_deployments_count >= gov.daily_change_budget ? "warn" : "accent"}
                  />
                </div>
                <Metric
                  className="border-b border-rule p-6 sm:border-b-0"
                  label="Approvers required"
                  value={gov.required_approver_count ?? "—"}
                  hint={gov.required_approver_count === null ? "Risk-tier default" : undefined}
                />
                <Metric
                  className="p-6"
                  label="Freeze window"
                  value={gov.freeze_window_start ? "Set" : "None"}
                  hint={gov.freeze_window_start ? "Scheduled" : "No window active"}
                />
              </dl>
              {data.site && (
                <details className="group border-t border-rule">
                  <summary className="flex items-center gap-2 px-6 py-3 text-[13px] font-medium text-ink-soft transition-colors hover:text-ink">
                    <span data-chevron aria-hidden="true" className="inline-block text-accent transition-transform">&rsaquo;</span>
                    Adjust governance
                  </summary>
                  <div className="grid gap-6 border-t border-rule bg-sunk/60 px-6 py-5 md:grid-cols-2">
                    {/*
                      Changeable here, where it used to be changeable only by an
                      operator token that no longer exists. The reason is
                      required by the API, not decoration: it is recorded like a
                      freeze.
                    */}
                    <form action={setSiteModeAction} className="flex flex-col gap-2">
                      <input type="hidden" name="site_host" value={target.host} />
                      <input type="hidden" name="site_id" value={data.site.id} />
                      <p className="eyebrow">Operation mode</p>
                      {/* The balloon lives on a wrapper: ::before and ::after do
                          not apply to replaced elements, so a tooltip set
                          directly on a <select> or <input> draws nothing. */}
                      <span
                        className="block"
                        data-tip="How far this site may go on its own. Observe measures only; recommend drafts proposals for approval; autopilot deploys unattended."
                      >
                        <select
                          name="mode"
                          defaultValue={gov.mode}
                          aria-label="Operation mode"
                          className={input}
                         aria-describedby="tip-8dcda9ad6f">
                          <option value="observe">observe — measure only</option>
                          <option value="recommend">recommend — deploy with approval</option>
                          <option value="autopilot">autopilot — deploy unattended</option>
                        </select>
                      </span>
                      <div className="flex gap-2">
                        <span
                          className="block flex-1"
                          data-tip="Why you are changing the mode. Required by the API and recorded in the audit trail against your name."
                        >
                          <input
                            name="reason"
                            required
                            minLength={3}
                            placeholder="Why this change"
                            aria-label="Reason for the mode change"
                            className={input}
                           aria-describedby="tip-266be79a7f"/>
                        </span>
                        <button
                          type="submit"
                          className={button.secondary}
                          data-tip="Apply the selected mode. Recorded with your reason; nothing publishes without approval unless you chose autopilot."
                         aria-describedby="tip-6f07753cce">
                          Set mode
                        </button>
                      </div>
                    </form>
                    {/*
                      Raising is always allowed. Lowering stops at the tier floor
                      for anything touching canonical, robots or redirect
                      directives. The count is frozen into a proposal when it is
                      drafted, so a change here governs the next draft only.
                    */}
                    <form action={setApproverCountAction} className="flex flex-col gap-2">
                      <input type="hidden" name="site_host" value={target.host} />
                      <input type="hidden" name="site_id" value={data.site.id} />
                      <p className="eyebrow">Approvers required</p>
                      <div className="flex gap-2">
                        <span
                          className="block flex-1"
                          data-tip="How many different people must approve a change before it can deploy. An author can never approve their own."
                        >
                          <input
                            name="required_approver_count"
                            type="number"
                            min={1}
                            max={5}
                            defaultValue={gov.required_approver_count ?? ""}
                            placeholder="default"
                            aria-label="Approvers required"
                            className={inputMono}
                           aria-describedby="tip-a909e65d70"/>
                        </span>
                        <button
                          type="submit"
                          className={button.secondary}
                         data-tip="Save this approver count. It applies to proposals drafted from now on, not to ones already waiting." aria-describedby="tip-c08dbcd0f5">
                          Set
                        </button>
                      </div>
                    </form>
                  </div>
                </details>
              )}
            </Panel>
          </section>
        )}

        {!data.site ? (
          <Panel className="dot-field px-6 py-16 text-center">
            <h2 className="font-display text-[28px] font-light tracking-tight text-ink">No site selected</h2>
            <p className="mx-auto mt-3 max-w-md text-[13px] leading-6 text-pretty text-ink-soft">
              Add a site you control and prove it with a DNS record. Adding one lives in settings,
              because it needs the address and a name only you can give.
            </p>
            <Link
              href="/settings/sites"
              className={cn(button.primary, "mt-6")}
            data-tip="Add a domain you control and prove it with a DNS record." aria-describedby="tip-26b5b277ea">
              Add a site
            </Link>
          </Panel>
        ) : data.site.status !== "active" ? (
          <section aria-labelledby="dns-heading" className="flex flex-col gap-6">
            <SectionHead
              index="01"
              id="dns-heading"
              title="Prove you own this domain"
              description={`Add the following DNS TXT record to verify ownership of ${data.site.canonical_origin}.`}
            />
            <Panel className="flex flex-col gap-5 p-6">
              {selectedChallenge ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <Copyable label="Host" value={selectedChallenge.record_name} />
                  <Copyable label="Value" value={selectedChallenge.record_value} />
                </div>
              ) : (
                <Note tone="warn">Generate a fresh site-specific TXT token before verification.</Note>
              )}
              <div className="flex flex-wrap gap-2">
                <form action={verifyPortfolioDns}>
                  <input type="hidden" name="site_host" value={target.host} />
                  <button type="submit" className={button.primary} data-tip="Check DNS now for the TXT record shown above. Safe to retry while it propagates." aria-describedby="tip-93fc91618b">
                    Verify DNS record
                  </button>
                </form>
                <form action={refreshDnsChallenge}>
                  <input type="hidden" name="site_host" value={target.host} />
                  <button type="submit" className={button.secondary} data-tip="Issue a fresh verification token. The previous record stops working immediately." aria-describedby="tip-ad2bc41307">
                    Regenerate token
                  </button>
                </form>
              </div>
            </Panel>
            <Panel className="p-6">
              <h3 className="font-display text-[17px] font-medium tracking-tight text-ink">DNS provider assistant</h3>
              <p className="mt-1.5 max-w-2xl text-[13px] leading-6 text-pretty text-ink-soft">
                The manual TXT record above works with every DNS host. This optional assistant adds provider-specific support without changing the consent policy.
              </p>
              {data.dnsProviderConnector?.status === "active" ? (
                <form action={createDnsProviderVerification} className="mt-4">
                  <input type="hidden" name="site_host" value={target.host} />
                  <input type="hidden" name="provider_key" value={data.dnsProviderConnector.provider_key ?? ""} />
                  <button type="submit" className={button.primary}>
                    Create this TXT record with {data.dnsProviderConnector.provider_key ?? "your DNS provider"}
                  </button>
                </form>
              ) : (
                <form action={connectDnsProvider} className="mt-4 grid gap-4 sm:grid-cols-3">
                  <Field label="Provider">
                    <select name="provider_key" defaultValue="cloudflare" className={input}>
                      <option value="cloudflare">Cloudflare (available now)</option>
                    </select>
                  </Field>
                  <Field label="Zone ID">
                    <input required name="zone_id" pattern="[A-Fa-f0-9]{32}" className={inputMono} />
                  </Field>
                  <Field label="Scoped API token">
                    <input required name="api_token" type="password" autoComplete="off" className={input} />
                  </Field>
                  <input type="hidden" name="site_host" value={target.host} />
                  <p className="text-[12px] leading-5 text-pretty text-ink-faint sm:col-span-3">
                    The selected adapter states its least-privilege scope. Connecting does not create or change DNS records; creation requires a second approval.
                  </p>
                  <button type="submit" className={cn(button.secondary, "justify-self-start sm:col-span-3")}>Connect selected provider</button>
                </form>
              )}
            </Panel>
          </section>
        ) : (
          <>
            {/* 01 — Evidence */}
            <section aria-labelledby="evidence-heading" className="flex flex-col gap-6">
              <SectionHead
                index="01"
                id="evidence-heading"
                title="Evidence"
                description="Read-only instruments. Each gathers evidence about the site and changes nothing on it."
              />
              <div className="grid gap-4 lg:grid-cols-3">
                <div className="lg:col-span-2">
                  <CrawlPanel crawls={data.crawls ?? []} now={data.renderedAt ?? new Date().toISOString()}>
                    <form action={startFirstCrawl}>
                      <input type="hidden" name="site_host" value={target.host} />
                      <button type="submit" className={crawlActive ? smallButton.secondary : smallButton.primary} data-tip="Start a bounded crawl of this site now. Read-only: it gathers evidence and changes nothing on the site." aria-describedby="tip-e3ef53a7eb">
                        Trigger new crawl
                      </button>
                    </form>
                  </CrawlPanel>
                </div>
                <Panel className="flex flex-col divide-y divide-rule">
                  <div className="flex flex-1 flex-col justify-between gap-4 p-6">
                    <div>
                      <p className="eyebrow">Search Console</p>
                      <p className="mt-2 text-[13px] leading-6 text-ink-soft tabular">
                        {data.searchPerformance ? `${data.searchPerformance.clicks} clicks · ${data.searchPerformance.impressions} impressions` : "Google Search Console ready."}
                      </p>
                    </div>
                    <form action={connectSearchConsole}>
                      <input type="hidden" name="site_host" value={target.host} />
                      <button type="submit" className={smallButton.secondary}>
                        {data.searchConsoleConnector ? "Re-sync Search Console" : "Connect Search Console"}
                      </button>
                    </form>
                  </div>
                  <div className="flex flex-1 flex-col justify-between gap-4 p-6">
                    <div>
                      <p className="eyebrow">Mobile Lighthouse lab</p>
                      <p className="mt-2 text-[13px] leading-6 text-ink-soft tabular">
                        {data.performanceSummary
                          ? `${data.performanceSummary.sample_count}/${data.performanceSummary.required_sample_count} samples · ${data.performanceSummary.status}`
                          : data.performanceRun?.observation
                            ? `Latest lab score: ${data.performanceRun.observation.performance_score}/100`
                            : "No lab sample yet. This is not field Core Web Vitals data."}
                      </p>
                      {data.performanceSummary ? (
                        <Meter
                          className="mt-3"
                          label="Lab samples collected"
                          value={data.performanceSummary.required_sample_count ? data.performanceSummary.sample_count / data.performanceSummary.required_sample_count : 0}
                        />
                      ) : null}
                    </div>
                    <form action={startPerformanceRun}>
                      <input type="hidden" name="site_host" value={target.host} />
                      <input type="hidden" name="idempotency_key" value={performanceIdempotencyKey} />
                      <button type="submit" className={smallButton.secondary} data-tip="Run one mobile Lighthouse sample. Lab data, not real-user Core Web Vitals." aria-describedby="tip-728aeb6e5d">
                        Run mobile lab sample
                      </button>
                    </form>
                  </div>
                </Panel>
              </div>
            </section>

            {/* 02 — Search and visitors */}
            <section aria-labelledby="visitor-data-heading" className="flex flex-col gap-6">
              <SectionHead
                index="02"
                id="visitor-data-heading"
                title="Search and visitors"
                description={
                  <>
                    Read-only, from this site&rsquo;s own Google properties
                    {data.searchPerformance ? `, ${data.searchPerformance.range_start} to ${data.searchPerformance.range_end}` : ""}.
                    Search Console counts searches; Analytics counts visits. They measure different things, so neither is subtracted from the other.
                  </>
                }
              />
              <div className="grid gap-4 md:grid-cols-2">
                <Panel className="p-6">
                  <h3 className="font-display text-[15px] font-medium tracking-tight text-ink">Google Search Console</h3>
                  {data.searchPerformance && data.searchPerformance.rows > 0 ? (
                    <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-8">
                      <Metric label="Clicks" value={data.searchPerformance.clicks} />
                      <Metric label="Impressions" value={data.searchPerformance.impressions} />
                      <Metric label="Click-through rate" value={data.searchPerformance.ctr === null ? "—" : (data.searchPerformance.ctr * 100).toFixed(1)} unit={data.searchPerformance.ctr === null ? undefined : "%"} />
                      <Metric label="Average position" value={data.searchPerformance.position === null ? "—" : data.searchPerformance.position.toFixed(1)} />
                    </dl>
                  ) : (
                    <p className="mt-4 text-[13px] leading-6 text-pretty text-ink-faint">No Search Console data yet. Connect Google under Settings, then wait for the daily sync.</p>
                  )}
                </Panel>
                <Panel className="p-6">
                  <h3 className="font-display text-[15px] font-medium tracking-tight text-ink">Google Analytics 4</h3>
                  {data.engagement && data.engagement.rows > 0 ? (
                    <>
                      <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-8">
                        <Metric label="Sessions" value={data.engagement.sessions} />
                        <Metric label="Engagement rate" value={data.engagement.engagement_rate === null ? "—" : (data.engagement.engagement_rate * 100).toFixed(0)} unit={data.engagement.engagement_rate === null ? undefined : "%"} />
                        <Metric label="Page views" value={data.engagement.views} />
                        <Metric label="Key events" value={data.engagement.key_events} />
                      </dl>
                      <table className="mt-8 w-full text-left text-[12px]">
                        <caption className="eyebrow mb-2 text-left">Top landing pages</caption>
                        <thead>
                          <tr className="text-ink-faint">
                            <th scope="col" className="py-1.5 font-normal">Page</th>
                            <th scope="col" className="py-1.5 text-right font-normal">Sessions</th>
                            <th scope="col" className="py-1.5 text-right font-normal">Engaged</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.engagement.top_landing_pages.map((page) => (
                            <tr key={page.landing_page} className="border-t border-rule">
                              <td className="max-w-0 truncate py-2 pr-4 font-mono text-ink">{page.landing_page}</td>
                              <td className="py-2 text-right font-mono text-ink">{page.sessions}</td>
                              <td className="py-2 text-right font-mono text-ink-soft">{page.engaged_sessions}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  ) : (
                    <p className="mt-4 text-[13px] leading-6 text-pretty text-ink-faint">No Analytics data yet. Connect Google under Settings; GA4 collects nothing from before its tag went live.</p>
                  )}
                </Panel>
              </div>
            </section>

            {/* 03 — Advisory queue */}
            <section aria-labelledby="advisory-heading" className="flex flex-col gap-6">
              <SectionHead
                index="03"
                id="advisory-heading"
                title="Advisory queue"
                description="Ranked candidates only. Every correction must pass evidence review, validation, and explicit human approval before a certified connector may act."
                aside={<Badge tone="warn">0 automatic changes</Badge>}
              />
              {data.notRechecked ? (
                <Note tone="neutral">
                  <span className="tabular">{data.notRechecked}</span>{" "}
                  {data.notRechecked === 1 ? "issue" : "issues"} found by an earlier crawl{" "}
                  {data.notRechecked === 1 ? "was" : "were"} not re-checked by the latest one, usually
                  because it did not reach {data.notRechecked === 1 ? "that page" : "those pages"}.{" "}
                  {data.notRechecked === 1 ? "It is" : "They are"} left out of this queue, not marked as fixed.
                </Note>
              ) : null}
              <Panel className="overflow-hidden">
                {data.opportunities.length === 0 ? (
                  <div className="px-6 py-14 text-center">
                    <p className="font-display text-[17px] font-light text-ink">No ranked advisory candidates yet.</p>
                    <p className="mt-2 text-[13px] text-ink-faint">Complete a bounded crawl and analysis to generate the top 20 evidence-backed opportunities.</p>
                  </div>
                ) : (
                  <ol className="divide-y divide-rule">
                    {data.opportunities.map((opportunity, index) => {
                      const advice = advisoryFor(opportunity.title);
                      return (
                        <li key={opportunity.id} className="grid grid-cols-[2.5rem_1fr] gap-x-4 px-6 py-5 transition-colors hover:bg-sunk/50 sm:grid-cols-[3rem_1fr_auto]">
                          <span className="figure pt-0.5 text-[26px] text-ink-faint">
                            {String(index + 1).padStart(2, "0")}
                          </span>
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge tone="neutral">{opportunity.type.replaceAll("_", " ")}</Badge>
                              <Badge tone={riskTone(opportunity.risk)}>{opportunity.risk} risk</Badge>
                            </div>
                            <h3 className="mt-2.5 text-[14px] leading-6 font-medium text-pretty text-ink">{opportunity.title}</h3>
                            {opportunity.page_url && (
                              <p className="mt-1 font-mono text-[12px] break-all text-accent">{opportunity.page_url}</p>
                            )}
                            <details className="mt-3">
                              <summary className="inline-flex items-center gap-1.5 text-[12px] font-medium text-ink-soft transition-colors hover:text-ink">
                                <span data-chevron aria-hidden="true" className="inline-block text-accent transition-transform">&rsaquo;</span>
                                Correction and validation
                              </summary>
                              <div className="mt-3 grid gap-4 rounded-xl bg-sunk/70 p-4 sm:grid-cols-2">
                                <div>
                                  <p className="eyebrow">Suggested correction</p>
                                  <p className="mt-1.5 text-[12px] leading-5 text-pretty text-ink-soft">{advice.correction}</p>
                                </div>
                                <div>
                                  <p className="eyebrow">Required validation</p>
                                  <p className="mt-1.5 text-[12px] leading-5 text-pretty text-ink-soft">{advice.validation}</p>
                                </div>
                              </div>
                            </details>
                          </div>
                          <div className="col-start-2 mt-4 flex flex-col gap-3 sm:col-start-3 sm:mt-0 sm:w-52 sm:items-end">
                            <div className="w-full">
                              <div className="flex items-baseline justify-between font-mono text-[11px] text-ink-faint tabular">
                                <span>Score</span>
                                <span className="text-[13px] text-ink">{Math.round(opportunity.score)}</span>
                              </div>
                              <Meter className="mt-1.5" label="Opportunity score" value={opportunity.score / 100} />
                              <p className="mt-1.5 text-right font-mono text-[11px] text-ink-faint tabular">
                                {Math.round(opportunity.confidence * 100)}% confidence
                              </p>
                            </div>
                            {/*
                              Drafting is not approving. This builds the change the
                              opportunity implies and submits it for review; it
                              writes nothing to the site.
                            */}
                            <form action={draftProposalAction}>
                              <input type="hidden" name="site_host" value={target.host} />
                              <input type="hidden" name="opportunity_id" value={opportunity.id} />
                              <button
                                type="submit"
                                className={smallButton.secondary}
                               data-tip="Turn this opportunity into a reviewable proposal with an exact diff. Publishes nothing." aria-describedby="tip-6d09637632">
                                Draft proposal
                              </button>
                            </form>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                )}

                <div className="flex flex-col gap-3 border-t border-rule bg-sunk/50 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <p className="text-[13px] font-medium text-ink">Human quality calibration</p>
                    <p className="text-[12px] text-ink-faint tabular">
                      {data.calibration
                        ? `${data.calibration.summary.reviewed}/${data.calibration.target_size} reviewed${data.calibration.summary.precision === null ? "" : ` · ${Math.round(data.calibration.summary.precision * 100)}% precision`}`
                        : "Freeze a reproducible 20-item review set before trusting advisory accuracy."}
                    </p>
                  </div>
                  {!data.calibration && data.opportunities.length > 0 && (
                    <form action={createCalibrationSet}>
                      <input type="hidden" name="site_host" value={target.host} />
                      <input type="hidden" name="idempotency_key" value={randomUUID()} />
                      <button type="submit" className={smallButton.secondary}>
                        Freeze 20-item human review set
                      </button>
                    </form>
                  )}
                </div>
              </Panel>
            </section>

            {/* 04 — Proposals */}
            <section aria-labelledby="proposals-heading" className="flex flex-col gap-6">
              <SectionHead
                index="04"
                id="proposals-heading"
                title="Proposals and review"
                description="Auditable diffs and policy evaluations. Deploying opens a pull request; a person still merges it."
                aside={<Badge tone="neutral">{data.proposals.length} proposals</Badge>}
              />
              {data.proposals.length === 0 ? (
                <Panel className="px-6 py-12 text-center">
                  <p className="text-[13px] text-ink-faint">No active proposals generated yet. Run analysis to create proposals.</p>
                </Panel>
              ) : (
                <div className="flex flex-col gap-4">
                  {data.proposals.map((prop) => (
                    <Panel as="article" key={prop.id} className="overflow-hidden">
                      <div className="flex flex-wrap items-start justify-between gap-3 px-6 pt-5">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge tone={riskTone(prop.risk)}>{prop.risk}</Badge>
                            <Badge tone="neutral">{prop.status.replaceAll("_", " ")}</Badge>
                          </div>
                          <h3 className="mt-3 font-display text-[17px] font-medium tracking-tight text-pretty text-ink">{prop.title}</h3>
                          <p className="mt-1.5 max-w-3xl text-[13px] leading-6 text-pretty text-ink-soft">{prop.rationale}</p>
                        </div>
                      </div>
                      <div className="grid gap-4 px-6 py-5 lg:grid-cols-2">
                        <SerpPreview
                          title={prop.title}
                          url={prop.page_url ?? `${target.origin}/${prop.target_path.replace(/^\//, "")}`}
                          description={prop.rationale}
                          isModified={true}
                        />
                        {prop.diff_unified && (
                          <pre className="max-h-72 overflow-auto rounded-xl border border-rule bg-sunk p-4 font-mono text-[12px] leading-5 text-ink-soft">
                            {prop.diff_unified}
                          </pre>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-rule px-6 py-3">
                        <span className="font-mono text-[12px] break-all text-ink-faint">{prop.target_path}</span>
                        <div className="flex gap-2">
                          {/*
                            `review_required` as well as `validated`: a proposal
                            needing two approvers stays `review_required` until
                            enough approvals arrive.
                          */}
                          {(prop.status === "validated" || prop.status === "review_required") && (
                            <>
                              <form action={approveProposalAction}>
                                <input type="hidden" name="site_host" value={target.host} />
                                <input type="hidden" name="proposal_id" value={prop.id} />
                                <button type="submit" className={smallButton.primary} data-tip="Record your approval. You cannot approve a change you authored yourself." aria-describedby="tip-ec0ccb8c77">
                                  Approve
                                </button>
                              </form>
                              {/* Withdrawing ends the proposal and can never put
                                  anything on a site. */}
                              <form action={withdrawProposalAction}>
                                <input type="hidden" name="site_host" value={target.host} />
                                <input type="hidden" name="proposal_id" value={prop.id} />
                                <button type="submit" className={smallButton.secondary} data-tip="Take this proposal off the table. It stops counting toward approvals and cannot deploy." aria-describedby="tip-5be8fcba56">
                                  Withdraw
                                </button>
                              </form>
                            </>
                          )}
                          {/*
                            Deploying opens a pull request. It does not merge: the
                            adapter has never had merge authority, so the change
                            reaches the live site only when a person merges it.
                          */}
                          {prop.status === "approved" && (
                            <form action={deployProposalAction}>
                              <input type="hidden" name="site_host" value={target.host} />
                              <input type="hidden" name="proposal_id" value={prop.id} />
                              {/* Keyed on the proposal, so a double click or a
                                  retry is the same intent, not a second PR. */}
                              <input type="hidden" name="idempotency_key" value={`deploy-${prop.id}`} />
                              <button type="submit" className={smallButton.primary} data-tip="Open the pull request that carries this change on your repository. A human still merges it." aria-describedby="tip-8840e44e3f">
                                Deploy
                              </button>
                            </form>
                          )}
                          {/* Rollback opens a revert pull request, and the
                              reconciler watches whether anybody merges it. */}
                          {prop.status === "deployed" && (
                            <form action={rollbackProposalAction}>
                              <input type="hidden" name="site_host" value={target.host} />
                              <input type="hidden" name="proposal_id" value={prop.id} />
                              <button type="submit" className={smallButton.caution} data-tip="Open a revert pull request. The change is only undone once someone merges it." aria-describedby="tip-638cd2f7c3">
                                Request revert
                              </button>
                            </form>
                          )}
                        </div>
                      </div>
                    </Panel>
                  ))}
                </div>
              )}
            </section>

            {/* 05 — Outcomes */}
            <section aria-labelledby="outcomes-heading" className="flex flex-col gap-6">
              <SectionHead
                index="05"
                id="outcomes-heading"
                title="28-day outcomes"
                description="Before-and-after association after independent deployment verification; this does not establish causation."
                aside={<Badge tone="neutral">{data.measurements.length} series</Badge>}
              />
              {data.measurements.length === 0 ? (
                <Panel className="px-6 py-12 text-center">
                  <p className="text-[13px] text-ink-faint">No measurement series recorded yet. Deployed proposals will track here over 28-day windows.</p>
                </Panel>
              ) : (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  {data.measurements.map((m) => (
                    <Panel key={m.id} className="p-6">
                      <div className="flex items-center justify-between gap-3">
                        <p className="eyebrow">Evidence completeness</p>
                        <span className="font-mono text-[12px] text-ink tabular">{Math.round(m.confidence_score * 100)}%</span>
                      </div>
                      <Meter className="mt-2" label="Evidence completeness" value={m.confidence_score} tone="good" />
                      <dl className="mt-6 grid grid-cols-2 gap-6">
                        <Metric
                          label="Clicks delta"
                          value={
                            typeof m.delta_metrics?.clicks_delta === "number"
                              ? m.delta_metrics.clicks_delta >= 0
                                ? `+${m.delta_metrics.clicks_delta}`
                                : m.delta_metrics.clicks_delta
                              : "N/A"
                          }
                        />
                        <Metric
                          label="Position delta"
                          value={
                            typeof m.delta_metrics?.position_delta === "number"
                              ? m.delta_metrics.position_delta >= 0
                                ? `+${m.delta_metrics.position_delta}`
                                : m.delta_metrics.position_delta
                              : "N/A"
                          }
                          unit={typeof m.delta_metrics?.position_delta === "number" ? "ranks" : undefined}
                        />
                      </dl>
                      {m.annotations?.length > 0 && (
                        <p className="mt-5 border-t border-rule pt-4 text-[12px] leading-5 text-pretty text-ink-faint">{m.annotations[0]}</p>
                      )}
                    </Panel>
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
