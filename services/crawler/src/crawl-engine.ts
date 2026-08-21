import {createHash} from "node:crypto";

import {load} from "cheerio";
import {XMLParser} from "fast-xml-parser";
import robotsParserModule from "robots-parser";

import type {FetchResource, LinkObservation, PageObservation} from "./types.js";

const USER_AGENT = "SEOAutopilotBot";
const MAX_LINKS_PER_PAGE = 5_000;
type RobotsPolicy = {isAllowed(url: string, userAgent?: string): boolean | undefined};
const parseRobots = robotsParserModule as unknown as (url: string, body: string) => RobotsPolicy;

export type CrawlResult = {
  observations: PageObservation[];
  skippedByRobots: number;
  fetchErrors: number;
  discoveryTruncated: boolean;
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

export async function crawlSite(
  origin: string,
  maxPages: number,
  fetchResource: FetchResource,
  maxDepth = 10,
): Promise<CrawlResult> {
  const root = new URL(origin);
  const host = root.hostname.toLowerCase();
  const discoveryBudget = Math.max(maxPages, maxPages * 10);
  const robotsUrl = new URL("/robots.txt", root);
  let robotsText = "";
  try { robotsText = (await fetchResource(robotsUrl)).body; } catch { robotsText = ""; }
  const policy = parseRobots(robotsUrl.toString(), robotsText);
  const sitemapCandidates = new Set<string>([new URL("/sitemap.xml", root).toString()]);
  for (const line of robotsText.split(/\r?\n/)) {
    if (sitemapCandidates.size >= 20) break;
    const match = /^sitemap:\s*(\S+)/i.exec(line);
    if (match?.[1]) sitemapCandidates.add(match[1]);
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
  for (const sitemap of sitemapCandidates) {
    const normalized = normalizeCandidate(sitemap, root, host);
    if (!normalized) continue;
    try {
      const sitemapUrls = extractSitemapUrls(
        (await fetchResource(new URL(normalized))).body,
        discoveryBudget,
      );
      if (sitemapUrls.length >= discoveryBudget) discoveryTruncated = true;
      for (const candidate of sitemapUrls) {
        enqueue(candidate, root, 0);
      }
    } catch { /* absent or malformed sitemaps are non-fatal */ }
  }
  const observations: PageObservation[] = [];
  let skippedByRobots = 0;
  let fetchErrors = 0;
  while (queue.length && observations.length < maxPages) {
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
        anchorText: $(element).text().replace(/\s+/g, " ").trim().slice(0, 500),
        relValues: [...new Set(($(element).attr("rel") ?? "").toLowerCase().split(/\s+/).filter(Boolean))],
      });
    }
    const text = $("body").text().replace(/\s+/g, " ").trim();
    observations.push({
      normalizedUrl: current,
      finalUrl: resource.finalUrl,
      status: resource.status,
      title: $("title").first().text().trim() || null,
      metaDescription: $('meta[name="description"]').attr("content")?.trim() || null,
      h1: $("h1").toArray().map(element => $(element).text().replace(/\s+/g, " ").trim()).filter(Boolean),
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
  return {observations, skippedByRobots, fetchErrors, discoveryTruncated};
}
