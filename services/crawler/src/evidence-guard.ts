import type {PageObservation} from "./types.js";

/**
 * Below this many successful fetches the shape of the content is not evidence
 * of anything. A three-page site whose pages happen to share a body is a
 * plausible site; a five-hundred-page one is a login wall.
 */
const MIN_OBSERVATIONS = 10;

/** One body repeated across at least this share of pages is a shell, not a site. */
const MAX_TOP_SHARE = 0.8;

/** Fewer distinct bodies than this share of pages means most pages are copies. */
const MIN_DISTINCT_RATIO = 0.1;

export type ContentCollapse = {
  observations: number;
  distinctHashes: number;
  /** Distinct bodies over pages. 1.0 when every page differs. */
  distinctRatio: number;
  /** The most repeated body's share of pages. Low when every page differs. */
  topShare: number;
  collapsed: boolean;
};

/**
 * Detect a crawl that fetched many pages but recorded the same body on all of
 * them -- an app shell, a login wall or a soft 404 served under HTTP 200.
 *
 * Nothing else notices this. Every fetch succeeds, the crawl completes, and
 * analysis then reports the shell's missing h1 and thin content once per URL,
 * producing a large and entirely fictitious finding set. The only thing that
 * separates it from a real crawl is that the bodies do not differ, and
 * `content_hash` is already recorded per observation, so the check is free.
 *
 * Only 2xx responses count. Error pages legitimately share a body, and a crawl
 * that is mostly errors is already reported as partial through `fetchErrors`.
 */
export function assessContentCollapse(observations: readonly PageObservation[]): ContentCollapse {
  const counts = new Map<string, number>();
  let total = 0;
  for (const observation of observations) {
    if (observation.status < 200 || observation.status > 299) continue;
    counts.set(observation.contentHash, (counts.get(observation.contentHash) ?? 0) + 1);
    total += 1;
  }
  if (total === 0) {
    return {observations: 0, distinctHashes: 0, distinctRatio: 1, topShare: 0, collapsed: false};
  }
  const distinctHashes = counts.size;
  const biggestGroup = Math.max(...counts.values());
  const distinctRatio = distinctHashes / total;
  const topShare = biggestGroup / total;
  return {
    observations: total,
    distinctHashes,
    distinctRatio,
    topShare,
    collapsed: total >= MIN_OBSERVATIONS && (topShare >= MAX_TOP_SHARE || distinctRatio <= MIN_DISTINCT_RATIO),
  };
}
