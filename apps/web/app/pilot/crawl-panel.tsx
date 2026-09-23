import {Badge} from "@/app/components/ui";

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
 */
export function CrawlPanel({crawls, now}: {crawls: Crawl[]; now: string}) {
  const latest = crawls[0];
  if (!latest) {
    return (
      <section aria-labelledby="crawl-heading" className="rounded-[4px] border border-rule bg-paper p-4">
        <h3 id="crawl-heading" className="text-sm font-semibold text-ink">Crawl</h3>
        <p className="mt-1 text-xs text-ink-faint">No crawl yet. Trigger one below to gather this site&rsquo;s evidence.</p>
      </section>
    );
  }
  const active = ACTIVE.has(latest.status);
  const progress = active ? describeProgress(latest, now) : null;
  const outcome = active ? null : describeOutcome(latest);
  const summary = latest.result_summary ?? {};

  return (
    <section aria-labelledby="crawl-heading" className="rounded-[4px] border border-rule bg-paper p-4">
      {active ? <LiveRefresh /> : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="crawl-heading" className="text-sm font-semibold text-ink">Crawl</h3>
        {active ? (
          <Badge tone={progress?.stalled ? "warn" : "accent"}>
            {latest.status === "queued" ? "Queued" : progress?.stalled ? "Not responding" : "Running"}
          </Badge>
        ) : outcome ? (
          <Badge tone={outcome.tone}>{outcome.label}</Badge>
        ) : null}
      </div>

      {progress ? (
        <div className="mt-3" aria-live="polite">
          <p className="text-[13px] font-medium text-ink tabular-nums">{progress.headline}</p>
          <div
            role="progressbar"
            aria-label="Crawl progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress.percent ?? undefined}
            className="mt-2 h-2 overflow-hidden rounded-full bg-sunk"
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
          <p className={`mt-2 text-pretty text-xs tabular-nums ${progress.stalled ? "text-warn" : "text-ink-faint"}`}>
            {progress.detail}
          </p>
        </div>
      ) : outcome ? (
        <div className="mt-2">
          <p className={`text-pretty text-[13px] ${outcome.tone === "stop" ? "text-stop" : "text-ink"}`}>{outcome.reason}</p>
          <p className="mt-1 text-xs text-ink-faint tabular-nums">
            Finished {latest.finished_at ? when(latest.finished_at) : "—"}
            {took(latest) ? ` · took ${took(latest)}` : ""}
            {typeof summary.sitemap_urls_declared === "number" ? ` · sitemap lists ${summary.sitemap_urls_declared} pages` : ""}
          </p>
        </div>
      ) : null}

      {crawls.length > 1 ? (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-medium text-accent">Recent crawls</summary>
          <ul className="mt-2 divide-y divide-rule text-xs">
            {crawls.map((crawl) => {
              const result = describeOutcome(crawl);
              const pages = crawl.result_summary?.pages_observed;
              return (
                <li key={crawl.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 tabular-nums">
                  <span className="w-32 text-ink-faint">{when(crawl.created_at)}</span>
                  <Badge tone={result?.tone ?? "accent"}>{result?.label ?? crawl.status}</Badge>
                  <span className="text-ink-soft">
                    {typeof pages === "number" ? `${pages} pages` : "—"}
                    {took(crawl) ? ` · ${took(crawl)}` : ""}
                    {crawl.error_code ? ` · ${crawl.error_code}` : ""}
                  </span>
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
