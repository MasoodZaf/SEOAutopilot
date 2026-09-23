/**
 * What a crawl job means to the person watching it.
 *
 * The dashboard used to print "Last crawl: running" and nothing else, so a
 * thirteen-minute crawl looked exactly like a stuck one, and a crawl that
 * failed for a reason the product understood -- content_collapse -- showed
 * the word "failed" with no reason and no next step. Kept apart from the page
 * so every state can be tested without rendering.
 */

/** @typedef {"neutral" | "accent" | "good" | "warn" | "stop"} Tone */

// No heartbeat for this long means the crawler has gone quiet. It writes one
// every 10 seconds; the lease it renews lasts two minutes.
export const STALL_SECONDS = 90;

const num = (value) => (typeof value === "number" && Number.isFinite(value) ? value : 0);

/** "4m 05s", "52s", "1h 03m". */
export function describeDuration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

const seconds = (from, to) => (Date.parse(to) - Date.parse(from)) / 1000;

/**
 * Why a crawl ended the way it did, in words that say what to do next.
 * @param {{status: string, error_code: string | null, result_summary: Record<string, unknown>}} crawl
 * @returns {{label: string, tone: Tone, reason: string} | null}
 */
export function describeOutcome(crawl) {
  const summary = crawl.result_summary ?? {};
  const pages = num(summary.pages_observed);
  if (crawl.status === "completed") {
    return {label: "Completed", tone: "good", reason: `Crawled ${pages} ${pages === 1 ? "page" : "pages"} with no errors.`};
  }
  if (crawl.status === "partial") {
    const why = [];
    if (pages >= num(summary.max_pages) && num(summary.max_pages) > 0) {
      why.push(`it reached the ${num(summary.max_pages)}-page limit`);
    }
    if (num(summary.fetch_errors) > 0) {
      const n = num(summary.fetch_errors);
      why.push(`${n} ${n === 1 ? "page" : "pages"} could not be fetched`);
    }
    if (summary.discovery_truncated) why.push("the site links to more pages than one crawl follows");
    return {
      label: "Partly completed",
      tone: "warn",
      reason: `Crawled ${pages} pages, but ${why.length ? why.join(", and ") : "not everything could be read"}. The findings use what was read.`,
    };
  }
  if (crawl.status === "cancelled") {
    return {label: "Cancelled", tone: "neutral", reason: "This crawl was cancelled before it finished."};
  }
  if (crawl.status !== "failed") return null;

  switch (crawl.error_code) {
    case "content_collapse": {
      const share = Math.round(num(summary.content_top_share) * 100);
      const distinct = num(summary.distinct_content_hashes);
      return {
        label: "Failed",
        tone: "stop",
        reason:
          `Most pages came back with the same content (${share}% identical; ${distinct} distinct versions across ${pages} pages). ` +
          "That usually means the site served a loading screen or a block page to the crawler instead of the real page. " +
          "Nothing was analysed from this crawl, so no findings were drawn from bad evidence.",
      };
    }
    case "crawl_lease_lost":
    case "crawl_heartbeat_failed":
      return {
        label: "Failed",
        tone: "stop",
        reason: "The crawler lost contact with this crawl partway through, usually because it was restarted. Start a new crawl.",
      };
    default:
      return {
        label: "Failed",
        tone: "stop",
        reason: `The crawl stopped with an error${crawl.error_code ? ` (${crawl.error_code})` : ""}. Start a new crawl; if it fails again the same way, it needs investigating.`,
      };
  }
}

/**
 * Where a queued or running crawl is, as a bar and a sentence.
 * @param {{status: string, progress?: Record<string, unknown>, attempts?: number,
 *          created_at: string, started_at: string | null, last_heartbeat_at?: string | null}} crawl
 * @param {string} now ISO time the page was rendered
 */
export function describeProgress(crawl, now) {
  if (crawl.status === "queued") {
    return {
      percent: null,
      headline: "Waiting for the crawler",
      detail: `Queued ${describeDuration(seconds(crawl.created_at, now))} ago. It is usually picked up within a minute.`,
      stalled: false,
    };
  }
  if (crawl.status !== "running") return null;

  const p = crawl.progress ?? {};
  const elapsed = crawl.started_at ? describeDuration(seconds(crawl.started_at, now)) : null;
  const quiet = crawl.last_heartbeat_at ? seconds(crawl.last_heartbeat_at, now) : 0;
  const stalled = quiet > STALL_SECONDS;
  const attempt = num(crawl.attempts);
  const stallNote = stalled
    ? ` No word from the crawler for ${describeDuration(quiet)}. If it stays silent it is retried automatically${attempt ? ` (attempt ${attempt} of 3)` : ""}.`
    : "";
  const suffix = elapsed ? ` ${elapsed} elapsed.` : "";

  const fetched = num(p.fetched);
  const pending = num(p.pending);
  const total = Math.max(fetched + pending, 1);
  if (p.phase === "fetching") {
    // Fetching is most of the work; saving fills the last stretch.
    const percent = Math.min(85, Math.round((fetched / total) * 85));
    const errors = num(p.fetch_errors);
    return {
      percent,
      headline: `Fetching pages: ${fetched} of about ${fetched + pending}`,
      detail: `${errors ? `${errors} could not be fetched so far.` : "No fetch errors so far."}${suffix}${stallNote}`,
      stalled,
    };
  }
  if (p.phase === "saving") {
    const saved = num(p.saved);
    return {
      percent: 85 + Math.round((saved / Math.max(fetched, 1)) * 15),
      headline: `Saving pages: ${saved} of ${fetched}`,
      detail: `All pages fetched; storing what was found.${suffix}${stallNote}`,
      stalled,
    };
  }
  return {
    percent: p.phase === "discovering" ? 2 : null,
    headline: p.phase === "discovering" ? "Reading robots.txt and sitemaps" : "Crawling",
    detail: `${elapsed ? `${elapsed} elapsed.` : "Starting."}${stallNote}`,
    stalled,
  };
}
