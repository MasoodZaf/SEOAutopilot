import {Metric, Panel, button, input} from "@/app/components/ui";

import {saveBrandTerms} from "../actions";
import {bars, formatChange, scorecard} from "../visibility.mjs";

export type VisibilityTrend = Parameters<typeof scorecard>[0] & {
  brand_terms: string[];
  brand_terms_derived: boolean;
};

function Bars({series, label}: {series: (number | null)[]; label: string}) {
  const heights = bars(series);
  if (!heights.some((value) => value !== null)) return null;
  const width = 6;
  const gap = 3;
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${heights.length * (width + gap)} 28`}
      className="mt-3 h-7 w-full max-w-[180px]"
      preserveAspectRatio="none"
    >
      {heights.map((value, index) =>
        value === null ? null : (
          <rect
            key={index}
            x={index * (width + gap)}
            y={28 - Math.max(1, value * 28)}
            width={width}
            height={Math.max(1, value * 28)}
            rx={1}
            className={index === heights.length - 1 ? "fill-accent" : "fill-rule-strong"}
          />
        ),
      )}
    </svg>
  );
}

const percent = (value: number | null) => (value === null ? "—" : Math.round(value * 100).toString());

/**
 * What grows when nobody clicks: branded demand, searches answered on the
 * results page, direct visits and AI answers that name the site.
 */
export function VisibilityPanel({trend, host}: {trend?: VisibilityTrend; host: string}) {
  if (!trend) {
    return (
      <Panel className="p-6 md:col-span-2">
        <h3 className="font-display text-[15px] font-medium tracking-tight text-ink">Zero-click scorecard</h3>
        <p className="mt-4 text-[13px] leading-6 text-pretty text-ink-faint">The scorecard could not be loaded. Reload the page to try again.</p>
      </Panel>
    );
  }
  const card = scorecard(trend);
  const notConnected = "Not connected";
  return (
    <Panel className="p-6 md:col-span-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-display text-[15px] font-medium tracking-tight text-ink">Zero-click scorecard</h3>
        <p className="text-[12px] text-ink-faint">
          Latest 4 weeks against the 4 before. A trend, not a cause.
        </p>
      </div>
      <p className="mt-2 max-w-3xl text-[13px] leading-6 text-pretty text-ink-soft">
        Most searches end without a click, and AI answers settle questions before anyone visits. These are the signs of attention that clicks miss.
      </p>
      {card.sparse ? (
        <p className="mt-3 text-[12px] leading-5 text-pretty text-warn">
          Too little search data to read a trend yet; the figures below are mostly noise until impressions grow.
        </p>
      ) : null}

      <dl className="mt-6 grid gap-x-6 gap-y-8 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <Metric
            label="Branded impressions"
            value={card.branded.current === null ? "—" : Math.round(card.branded.current)}
            unit={formatChange(card.branded.change) || undefined}
            hint={
              !trend.search_connected
                ? notConnected
                : card.branded.current === null
                  ? "Search terms could not be read; the split is unknown."
                  : "Searches that name the site. People asking for it by name."
            }
          />
          <Bars series={card.branded.series} label="Branded impressions by week" />
        </div>
        <div>
          <Metric
            label="Answered on the results page"
            value={percent(card.zeroClick.current)}
            unit={card.zeroClick.current === null ? undefined : "% of impressions"}
            hint={
              trend.search_connected
                ? `${card.zeroClick.queries ?? 0} queries shown 5+ times a week with no click.`
                : notConnected
            }
          />
          <Bars series={card.zeroClick.series} label="Share of impressions with no click, by week" />
        </div>
        <div>
          <Metric
            label="Direct visits"
            value={card.direct.current === null ? "—" : Math.round(card.direct.current)}
            unit={formatChange(card.direct.change) || undefined}
            hint={trend.analytics_connected ? "GA4 sessions with no referrer: people who came back." : notConnected}
          />
          <Bars series={card.direct.series} label="Direct sessions by week" />
        </div>
        <div>
          <Metric
            label="AI answers naming the site"
            value={percent(card.ai.current)}
            unit={card.ai.current === null ? undefined : `% ${formatChange(card.ai.change, {points: true})}`.trim()}
            hint={
              card.ai.runs === 0
                ? "No citation run yet. Track questions in the agent workspace."
                : `Latest run, ${card.ai.answers} answers: cited in ${percent(card.ai.cited)}%, a tracked competitor cited in ${percent(card.ai.competitor)}%.`
            }
          />
          <Bars series={card.ai.series} label="Share of AI answers naming the site, by run" />
        </div>
      </dl>

      <details className="mt-8 border-t border-rule pt-4">
        <summary className="cursor-pointer text-[13px] text-ink-soft">
          Brand terms{" "}
          <span className="font-mono text-[12px] text-ink-faint">
            {trend.brand_terms.join(", ") || "none"}
            {trend.brand_terms_derived ? " (from the site's name and domain)" : ""}
          </span>
        </summary>
        <form action={saveBrandTerms} className="mt-3 flex flex-wrap items-end gap-2">
          <input type="hidden" name="site_host" value={host} />
          <label className="flex min-w-[240px] flex-1 flex-col gap-1 text-[12px] text-ink-faint" htmlFor="brand-terms">
            Names people search the site by, separated by commas. Leave empty to derive them again.
            <input
              id="brand-terms"
              name="brand_terms"
              defaultValue={trend.brand_terms_derived ? "" : trend.brand_terms.join(", ")}
              placeholder={trend.brand_terms.join(", ")}
              className={input}
              maxLength={600}
            />
          </label>
          <button type="submit" className={button.secondary}>
            Save brand terms
          </button>
        </form>
      </details>
    </Panel>
  );
}
