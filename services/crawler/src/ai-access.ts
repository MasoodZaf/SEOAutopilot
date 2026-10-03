/**
 * Which AI crawlers a site's robots.txt admits, and whether it serves llms.txt.
 *
 * Read from the robots.txt the crawl already fetched, evaluated per published
 * user-agent token. This is the site's declared policy only: a firewall or CDN
 * that blocks a bot by user agent is invisible here, and we never impersonate
 * another crawler to find out.
 *
 * Retrieval bots fetch pages to answer and cite a user's question; blocking
 * them removes the site from those answers. Training bots collect corpora;
 * blocking them is a legitimate choice with no bearing on citation, so they
 * are reported but never scored.
 */
import type {FetchResource} from "./types.js";

export type AiCrawlerPurpose = "retrieval" | "training";

export const AI_CRAWLERS: ReadonlyArray<{token: string; operator: string; purpose: AiCrawlerPurpose}> = [
  {token: "OAI-SearchBot", operator: "OpenAI", purpose: "retrieval"},
  {token: "ChatGPT-User", operator: "OpenAI", purpose: "retrieval"},
  {token: "Claude-SearchBot", operator: "Anthropic", purpose: "retrieval"},
  {token: "Claude-User", operator: "Anthropic", purpose: "retrieval"},
  {token: "PerplexityBot", operator: "Perplexity", purpose: "retrieval"},
  {token: "Perplexity-User", operator: "Perplexity", purpose: "retrieval"},
  // Google's AI Overviews and Microsoft Copilot answer from the ordinary index.
  {token: "Googlebot", operator: "Google", purpose: "retrieval"},
  {token: "Bingbot", operator: "Microsoft", purpose: "retrieval"},
  {token: "GPTBot", operator: "OpenAI", purpose: "training"},
  {token: "ClaudeBot", operator: "Anthropic", purpose: "training"},
  {token: "Google-Extended", operator: "Google", purpose: "training"},
  {token: "Applebot-Extended", operator: "Apple", purpose: "training"},
  {token: "CCBot", operator: "Common Crawl", purpose: "training"},
];

/** URLs evaluated per crawler; bounded so a huge crawl costs nothing extra. */
export const MAX_SAMPLED_URLS = 200;
const MAX_LLMS_TXT_BYTES = 512 * 1024;

export type RobotsPolicy = {isAllowed(url: string, userAgent?: string): boolean | undefined};

export type AiCrawlerAccess = {
  token: string;
  operator: string;
  purpose: AiCrawlerPurpose;
  /** Sampled URLs robots.txt admits for this token, homepage included. */
  allowed_urls: number;
  sampled_urls: number;
  homepage_allowed: boolean;
};

export type LlmsTxtStatus = "present" | "missing" | "invalid" | "unreachable" | "disallowed";

export type AiAccessReport = {
  schema_version: 1;
  robots_txt: "found" | "missing" | "unreachable";
  crawlers: AiCrawlerAccess[];
  /** Counts and shape only -- the file's text is untrusted and never stored. */
  llms_txt: {status: LlmsTxtStatus; bytes: number; links: number; has_title: boolean};
};

export function assessCrawlerAccess(policy: RobotsPolicy, homepage: string, urls: string[]): AiCrawlerAccess[] {
  const sample = [homepage, ...urls.filter(url => url !== homepage)].slice(0, MAX_SAMPLED_URLS);
  return AI_CRAWLERS.map(crawler => {
    // robots-parser returns undefined for a URL it cannot judge; treat that as
    // allowed, which is how a crawler reads the absence of a rule.
    const allowed = (url: string) => policy.isAllowed(url, crawler.token) !== false;
    return {
      ...crawler,
      allowed_urls: sample.filter(allowed).length,
      sampled_urls: sample.length,
      homepage_allowed: allowed(homepage),
    };
  });
}

/** Shape of an llms.txt per llmstxt.org: markdown opening with an H1 title. */
export function describeLlmsTxt(body: string): {valid: boolean; links: number; has_title: boolean} {
  const text = body.replace(/^﻿/, "").trimStart();
  const hasTitle = /^# \S/.test(text);
  const links = (text.match(/\]\([^)\s]+\)/g) ?? []).length;
  // An HTML page served at /llms.txt (a soft 404 or an app shell) is not one.
  const looksHtml = /^<(!doctype|html|head|body)\b/i.test(text);
  return {valid: hasTitle && !looksHtml, links, has_title: hasTitle};
}

export async function checkLlmsTxt(
  root: URL,
  policy: RobotsPolicy,
  userAgent: string,
  fetchResource: FetchResource,
): Promise<AiAccessReport["llms_txt"]> {
  const url = new URL("/llms.txt", root);
  const empty = {bytes: 0, links: 0, has_title: false};
  if (policy.isAllowed(url.toString(), userAgent) === false) return {status: "disallowed", ...empty};
  let resource;
  try {
    resource = await fetchResource(url);
  } catch {
    return {status: "unreachable", ...empty};
  }
  // A redirect to the homepage or a login page is not a served llms.txt.
  const landedElsewhere = new URL(resource.finalUrl).pathname !== "/llms.txt";
  if (resource.status === 404 || resource.status === 410 || landedElsewhere) return {status: "missing", ...empty};
  if (resource.status !== 200) return {status: "unreachable", ...empty};
  const body = resource.body.slice(0, MAX_LLMS_TXT_BYTES);
  // A single-page app answers every path with its HTML shell and a 200. That
  // is the site saying "no such file", not a malformed llms.txt, and treating
  // it as invalid would refuse the one fix the site needs.
  if (resource.contentType.includes("text/html") || /^\s*<(!doctype|html)\b/i.test(body)) {
    return {status: "missing", ...empty};
  }
  const shape = describeLlmsTxt(body);
  const bytes = Buffer.byteLength(resource.body);
  if (!shape.valid) {
    return {status: "invalid", bytes, links: shape.links, has_title: shape.has_title};
  }
  return {status: "present", bytes, links: shape.links, has_title: shape.has_title};
}
