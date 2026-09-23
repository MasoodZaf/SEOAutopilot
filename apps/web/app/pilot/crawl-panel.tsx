import {Badge, Panel} from "@/app/components/ui";

import {describeDuration, describeOutcome, describeProgress} from "./crawl-state.mjs";
import {LiveRefresh} from "./live-refresh";
import type {Crawl} from "./model";

const ACTIVE = new Set(["queued", "running"]);

const when = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  }) + " UTC";

const took = (crawl: Crawl) =>
  crawl.started_at && crawl.finished_at
    ? describeDuration((Date.parse(crawl.finished_at) - Date.parse(crawl.started_at)) / 1000)
    : null;

/**
 * The crawl in progress as a live bar, then how it ended and why, then the
 * last few crawls so a repeating failure is visible as a pattern.
 *
 * `children` is the control that starts a crawl. It lives here, beside the
 * crawl it would start, rather than in a separate card that repeated "a crawl
 * is in progress -- see above".
 */
export function CrawlPanel({crawls, now, children}: {crawls: Crawl[]; now: string; children?: React.ReactNode}) {
  const latest = crawls[0];
  const head = (badge: React.ReactNode) => (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        <h3 id="crawl-heading" className="eyebrow">Crawl</h3>
        {badge}
      </div>
      {children}
    </div>
  );
  if (!latest) {
    return (
      <Panel aria-labelledby="crawl-heading" className="h-full p-6">
        {head(null)}
        <p className="mt-6 font-display text-[22px] leading-tight font-light text-ink">No crawl yet.</p>
        <p className="mt-2 text-[13px] text-ink-faint">Trigger one to gather this site&rsquo;s evidence.</p>
      </Panel>
    );
  }
  const active = ACTIVE.has(latest.status);
  const progress = active ? describeProgress(latest, now) : null;
  const outcome = active ? null : describeOutcome(latest);
  const summary = latest.result_summary ?? {};
  const pages = summary.pages_observed;

  return (
    <Panel aria-labelledby="crawl-heading" className="flex h-full flex-col p-6">
      {active ? <LiveRefresh /> : null}
      {head(
        active ? (
          <Badge tone={progress?.stalled ? "warn" : "accent"}>
            {latest.status === "queued" ? "Queued" : progress?.stalled ? "Not responding" : "Running"}
          </Badge>
        ) : outcome ? (
          <Badge tone={outcome.tone}>{outcome.label}</Badge>
        ) : null,
      )}

      {progress ? (
        <div className="mt-6" aria-live="polite">
          <p className="font-display text-[22px] leading-tight font-light text-ink tabular-nums">{progress.headline}</p>
          <div
            role="progressbar"
            aria-label="Crawl progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress.percent ?? undefined}
            className="mt-4 h-1 overflow-hidden rounded-full bg-rule"
          >
            {progress.percent === null ? (
              <div className="h-full w-1/3 rounded-full bg-accent/60" />
            ) : (
              <div
                className={`h-full rounded-full transition-[width] duration-500 ${progress.stalled ? "bg-warn" : "bg-accent"}`}
                style={{width: `${Math.max(progress.percent, 2)}%`}}
              />
            )}
          </div>
          <p className={`mt-3 text-pretty text-[12px] tabular-nums ${progress.stalled ? "text-warn" : "text-ink-faint"}`}>
            {progress.detail}
          </p>
        </div>
      ) : outcome ? (
        <div className="mt-6 flex flex-wrap items-end gap-x-10 gap-y-4">
          {typeof pages === "number" ? (
            <div>
              <p className="figure text-[56px] text-ink">{pages}</p>
              <p className="eyebrow mt-2">Pages crawled</p>
            </div>
          ) : null}
          <div className="min-w-0 flex-1 pb-1">
            <p className={`text-pretty text-[14px] leading-6 ${outcome.tone === "stop" ? "text-stop" : "text-ink"}`}>{outcome.reason}</p>
            <p className="mt-1 text-[12px] text-ink-faint tabular-nums">
              Finished {latest.finished_at ? when(latest.finished_at) : "—"}
              {took(latest) ? ` · took ${took(latest)}` : ""}
              {typeof summary.sitemap_urls_declared === "number" ? ` · sitemap lists ${summary.sitemap_urls_declared} pages` : ""}
            </p>
          </div>
        </div>
      ) : null}

      {crawls.length > 1 ? (
        <details className="mt-auto pt-6">
          <summary className="inline-flex items-center gap-1.5 text-[12px] font-medium text-ink-soft transition-colors hover:text-ink">
            <span data-chevron aria-hidden="true" className="inline-block text-accent transition-transform">&rsaquo;</span>
            Recent crawls
          </summary>
          <ul className="mt-3 divide-y divide-rule text-[12px]">
            {crawls.map((crawl) => {
              const result = describeOutcome(crawl);
              const count = crawl.result_summary?.pages_observed;
              return (
                <li key={crawl.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 tabular-nums">
                  <span className="w-32 font-mono text-ink-faint">{when(crawl.created_at)}</span>
                  <Badge tone={result?.tone ?? "accent"}>{result?.label ?? crawl.status}</Badge>
                  <span className="text-ink-soft">
                    {typeof count === "number" ? `${count} pages` : "—"}
                    {took(crawl) ? ` · ${took(crawl)}` : ""}
                    {crawl.error_code ? ` · ${crawl.error_code}` : ""}
                  </span>
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}
    </Panel>
  );
}
