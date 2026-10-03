/**
 * The zero-click scorecard, from the API's weekly visibility trend.
 *
 * Each figure compares the latest four weeks with the four before them. That
 * is a description of a trend, never an attribution: nothing here says a
 * change caused it. Figures whose source is not connected are null so the page
 * can say "not connected" instead of drawing a zero.
 */

const WINDOW = 4;

const sum = (rows, key) => rows.reduce((total, row) => total + (row[key] ?? 0), 0);

/** @param {number | null} current @param {number | null} previous */
function change(current, previous) {
  if (current === null || previous === null) return null;
  if (previous === 0) return current === 0 ? 0 : null;
  return (current - previous) / previous;
}

/**
 * @param {{
 *   weeks: Array<{week_start: string, clicks: number, impressions: number,
 *     branded_clicks: number | null, branded_impressions: number | null,
 *     zero_click_queries: number, zero_click_impressions: number,
 *     direct_sessions: number | null, sessions: number | null}>,
 *   citation_runs: Array<{finished_at: string, answers: number, named: number, cited: number, competitor_cited: number}>,
 *   branded_available: boolean, search_connected: boolean, analytics_connected: boolean, sparse: boolean,
 * }} trend
 */
export function scorecard(trend) {
  const weeks = trend.weeks ?? [];
  const recent = weeks.slice(-WINDOW);
  const earlier = weeks.slice(-2 * WINDOW, -WINDOW);
  const branded = (rows) => (trend.branded_available ? sum(rows, "branded_impressions") : null);
  const zeroShare = (rows) => {
    const impressions = sum(rows, "impressions");
    return impressions ? sum(rows, "zero_click_impressions") / impressions : null;
  };
  const direct = (rows) => (trend.analytics_connected ? sum(rows, "direct_sessions") : null);

  const runs = (trend.citation_runs ?? []).filter((run) => run.answers > 0);
  const named = (run) => run.named / run.answers;
  const latest = runs.at(-1) ?? null;
  const prior = runs.at(-2) ?? null;

  return {
    weeks: weeks.length,
    sparse: Boolean(trend.sparse),
    branded: {
      current: trend.search_connected ? branded(recent) : null,
      change: trend.search_connected ? change(branded(recent), branded(earlier)) : null,
      series: weeks.map((row) => row.branded_impressions),
    },
    zeroClick: {
      current: trend.search_connected ? zeroShare(recent) : null,
      queries: trend.search_connected ? sum(recent, "zero_click_queries") : null,
      series: weeks.map((row) => (row.impressions ? row.zero_click_impressions / row.impressions : null)),
    },
    direct: {
      current: direct(recent),
      change: change(direct(recent), direct(earlier)),
      series: weeks.map((row) => row.direct_sessions),
    },
    ai: {
      current: latest ? named(latest) : null,
      change: latest && prior ? named(latest) - named(prior) : null,
      cited: latest ? latest.cited / latest.answers : null,
      competitor: latest ? latest.competitor_cited / latest.answers : null,
      answers: latest?.answers ?? 0,
      runs: runs.length,
      series: runs.map(named),
    },
  };
}

/** "+12%", "−8%", "0%", or "" when there is nothing to compare. */
export function formatChange(value, {points = false} = {}) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  const rounded = Math.round(value * 100);
  if (rounded === 0) return points ? "±0 pts" : "0%";
  const sign = rounded > 0 ? "+" : "−";
  return `${sign}${Math.abs(rounded)}${points ? " pts" : "%"}`;
}

/**
 * Bar heights, 0..1, for a small inline chart; null stays null (no bar).
 * @param {(number | null | undefined)[]} series
 * @returns {(number | null)[]}
 */
export function bars(series) {
  const values = /** @type {number[]} */ (series.filter((value) => value !== null && value !== undefined));
  const top = Math.max(0, ...values);
  return series.map((value) => (value === null || value === undefined ? null : top ? value / top : 0));
}
