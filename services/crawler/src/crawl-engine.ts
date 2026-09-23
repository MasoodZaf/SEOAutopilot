import {createHash} from "node:crypto";

import {load} from "cheerio";
import {XMLParser} from "fast-xml-parser";
import robotsParserModule from "robots-parser";

import type {FetchResource, LinkObservation, PageObservation} from "./types.js";

const USER_AGENT = "SEOAutopilotBot";

/**
 * Elements that end a word. Cheerio's `.text()` concatenates text nodes with
 * nothing between them, so `<h1>Every calculator<br>you'll ever need</h1>`
 * reads back as "calculatoryou" — one token where a reader sees two. That
 * corrupts every rule downstream: heading and title comparisons tokenize a
 * word that does not exist, and word counts fall well below what is on the page.
 */
const TEXT_BOUNDARY_SELECTOR =
  "br,p,div,li,tr,td,th,h1,h2,h3,h4,h5,h6,section,article,header,footer,nav," +
  "aside,blockquote,pre,figure,figcaption,option,dt,dd,hr,form,table";

/** Elements whose text is code or styling, never page copy. */
const NON_CONTENT_SELECTOR = "script,style,noscript,template";

type Fragment = ReturnType<ReturnType<typeof load>>;

function readVisibleText(fragment: Fragment): string {
  const copy = fragment.clone();
  copy.find(NON_CONTENT_SELECTOR).remove();
  copy.find(TEXT_BOUNDARY_SELECTOR).before(" ").after(" ");
  return copy.text().replace(/\s+/g, " ").trim();
}


const MAX_LINKS_PER_PAGE = 5_000;
type RobotsPolicy = {isAllowed(url: string, userAgent?: string): boolean | undefined};
const parseRobots = robotsParserModule as unknown as (url: string, body: string) => RobotsPolicy;

export type SitemapSource = {
  sitemapUrl: string;
  discoveredVia: "well_known" | "robots_txt";
  status: "fetched" | "unreachable" | "malformed" | "out_of_scope";
  declaredUrlCount: number;
  /** Declared URLs that survived same-host normalisation. */
  inScopeUrls: string[];
  truncated: boolean;
};

export type CrawlResult = {
  observations: PageObservation[];
  skippedByRobots: number;
  fetchErrors: number;
  discoveryTruncated: boolean;
  sitemaps: SitemapSource[];
};

export function normalizeCandidate(raw: string, base: URL, host: string): string | null {
  try {
    const url = new URL(raw, base);
    if (!["http:", "https:"].includes(url.protocol) || url.hostname.toLowerCase() !== host) return null;
    if (url.username || url.password || url.search) return null;
    url.hash = "";
    url.hostname = url.hostname.toLowerCase();
    if (url.pathname !== "/") url.pathname = url.pathname.replace(/\/+$/, "") || "/";
    return url.toString();
  } catch {
    return null;
  }
}

export function extractSitemapUrls(xml: string, maxUrls = 10_000): string[] {
  const parsed: unknown = new XMLParser({ignoreAttributes: false}).parse(xml);
  const locations: string[] = [];
  const visit = (value: unknown): void => {
    if (locations.length >= maxUrls) return;
    if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === "object") {
      for (const [key, child] of Object.entries(value)) {
        if (key === "loc" && typeof child === "string") locations.push(child.trim());
        else visit(child);
      }
    }
  };
  visit(parsed);
  return locations;
}

function resolveCanonical(raw: string | undefined, base: URL): string | null {
  if (!raw) return null;
  try {
    const url = new URL(raw, base);
    return ["http:", "https:"].includes(url.protocol) ? url.toString() : null;
  } catch {
    return null;
  }
}

function extractRobotsDirectives($: ReturnType<typeof load>): string[] {
  const values = $('meta[name="robots"],meta[name="googlebot"]').toArray().flatMap(element =>
    ($(element).attr("content") ?? "").split(",")
  );
  return [...new Set(values.map(value => value.trim().toLowerCase()).filter(Boolean))];
}

function extractStructuredData($: ReturnType<typeof load>): unknown[] {
  return $('script[type="application/ld+json"]').toArray().slice(0, 20).flatMap(element => {
    const raw = $(element).text().trim();
    if (!raw) return [];
    try {
      const value: unknown = JSON.parse(raw);
      return [value];
    } catch {
      return [];
    }
  });
}

/**
 * What a running crawl has done so far, for the progress bar.
 *
 * Mutated in place by `crawlSite` and read by the heartbeat, which writes it
 * to `crawl_job.progress`. Counts only -- never a URL, so nothing a site
 * serves ends up in a status payload.
 */
export type CrawlProgress = {
  phase: "discovering" | "fetching" | "saving";
  fetched: number;
  pending: number;
  fetch_errors: number;
  skipped_by_robots: number;
  saved: number;
  max_pages: number;
};

export function newProgress(maxPages: number): CrawlProgress {
  return {phase: "discovering", fetched: 0, pending: 0, fetch_errors: 0, skipped_by_robots: 0, saved: 0, max_pages: maxPages};
}

export async function crawlSite(
  origin: string,
  maxPages: number,
  fetchResource: FetchResource,
  maxDepth = 10,
  progress: CrawlProgress = newProgress(maxPages),
): Promise<CrawlResult> {
  const root = new URL(origin);
  const host = root.hostname.toLowerCase();
  const discoveryBudget = Math.max(maxPages, maxPages * 10);
  const robotsUrl = new URL("/robots.txt", root);
  let robotsText = "";
  try { robotsText = (await fetchResource(robotsUrl)).body; } catch { robotsText = ""; }
  const policy = parseRobots(robotsUrl.toString(), robotsText);
  const wellKnownSitemap = new URL("/sitemap.xml", root).toString();
  const sitemapCandidates = new Map<string, "well_known" | "robots_txt">([
    [wellKnownSitemap, "well_known"],
  ]);
  for (const line of robotsText.split(/\r?\n/)) {
    if (sitemapCandidates.size >= 20) break;
    const match = /^sitemap:\s*(\S+)/i.exec(line);
    if (match?.[1] && !sitemapCandidates.has(match[1])) sitemapCandidates.set(match[1], "robots_txt");
  }
  type QueueEntry = {url: string; depth: number};
  const queue: QueueEntry[] = [];
  const queued = new Set<string>();
  const seen = new Set<string>();
  let discoveryTruncated = false;
  const enqueue = (raw: string, base: URL, depth: number): string | null => {
    const normalized = normalizeCandidate(raw, base, host);
    if (!normalized || depth > maxDepth || seen.has(normalized) || queued.has(normalized)) return null;
    if (queued.size + seen.size >= discoveryBudget) {
      discoveryTruncated = true;
      return null;
    }
    queue.push({url: normalized, depth});
    queued.add(normalized);
    return normalized;
  };
  enqueue(root.toString(), root, 0);
  const sitemaps: SitemapSource[] = [];
  for (const [sitemap, discoveredVia] of sitemapCandidates) {
    const normalized = normalizeCandidate(sitemap, root, host);
    if (!normalized) {
      // A sitemap pointing off-host is recorded, never fetched.
      sitemaps.push({sitemapUrl:sitemap.slice(0,8192),discoveredVia,status:"out_of_scope",declaredUrlCount:0,inScopeUrls:[],truncated:false});
      continue;
    }
    let body: string;
    try {
      body = (await fetchResource(new URL(normalized))).body;
    } catch {
      sitemaps.push({sitemapUrl:normalized,discoveredVia,status:"unreachable",declaredUrlCount:0,inScopeUrls:[],truncated:false});
      continue;
    }
    let declared: string[];
    try {
      declared = extractSitemapUrls(body, discoveryBudget);
    } catch {
      sitemaps.push({sitemapUrl:normalized,discoveredVia,status:"malformed",declaredUrlCount:0,inScopeUrls:[],truncated:false});
      continue;
    }
    const truncated = declared.length >= discoveryBudget;
    if (truncated) discoveryTruncated = true;
    const inScope = new Set<string>();
    for (const candidate of declared) {
      const normalizedCandidate = normalizeCandidate(candidate, root, host);
      if (normalizedCandidate) inScope.add(normalizedCandidate);
      enqueue(candidate, root, 0);
    }
    sitemaps.push({sitemapUrl:normalized,discoveredVia,status:"fetched",declaredUrlCount:declared.length,inScopeUrls:[...inScope],truncated});
  }
  const observations: PageObservation[] = [];
  let skippedByRobots = 0;
  let fetchErrors = 0;
  progress.phase = "fetching";
  const report = () => {
    progress.fetched = observations.length;
    progress.pending = Math.min(queue.length, maxPages - observations.length);
    progress.fetch_errors = fetchErrors;
    progress.skipped_by_robots = skippedByRobots;
  };
  report();
  while (queue.length && observations.length < maxPages) {
    report();
    const entry = queue.shift();
    if (!entry) continue;
    const current = entry.url;
    queued.delete(current);
    if (seen.has(current)) continue;
    seen.add(current);
    if (policy.isAllowed(current, USER_AGENT) === false) { skippedByRobots += 1; continue; }
    let resource;
    try {
      resource = await fetchResource(new URL(current));
    } catch {
      fetchErrors += 1;
      continue;
    }
    if (!resource.contentType.includes("text/html")) continue;
    const $ = load(resource.body);
    const finalBase = new URL(resource.finalUrl);
    const links: LinkObservation[] = [];
    const linkElements = $("a[href]").toArray();
    for (const element of linkElements.slice(0, MAX_LINKS_PER_PAGE)) {
      const candidate = normalizeCandidate($(element).attr("href") ?? "", finalBase, host);
      if (!candidate) continue;
      enqueue(candidate, finalBase, entry.depth + 1);
      links.push({
        targetUrl: candidate,
        anchorText: readVisibleText($(element)).slice(0, 500),
        relValues: [...new Set(($(element).attr("rel") ?? "").toLowerCase().split(/\s+/).filter(Boolean))],
      });
    }
    const text = readVisibleText($("body"));
    observations.push({
      normalizedUrl: current,
      finalUrl: resource.finalUrl,
      status: resource.status,
      title: $("title").first().text().trim() || null,
      metaDescription: $('meta[name="description"]').attr("content")?.trim() || null,
      h1: $("h1").toArray().map(element => readVisibleText($(element))).filter(Boolean),
      wordCount: text ? text.split(" ").length : 0,
      contentHash: createHash("sha256").update(text).digest("hex"),
      rendered: resource.rendered,
      canonicalUrl: resolveCanonical($('link[rel~="canonical"]').first().attr("href"), finalBase),
      robotsDirectives: extractRobotsDirectives($),
      structuredData: extractStructuredData($),
      links,
      linkCountTotal: linkElements.length,
      linksTruncated: linkElements.length > MAX_LINKS_PER_PAGE,
    });
  }
  report();
  return {observations, skippedByRobots, fetchErrors, discoveryTruncated, sitemaps};
}
