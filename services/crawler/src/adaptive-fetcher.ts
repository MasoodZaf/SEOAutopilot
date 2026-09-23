import {load} from "cheerio";
import {chromium, type Browser, type Page} from "playwright";

import {createHttpFetcher} from "./http-fetcher.js";
import type {FetchedResource, FetchResource} from "./types.js";
import {assertSafeUrl} from "./url-policy.js";

export type RenderPolicy = "auto" | "always" | "never";
export type ManagedFetcher = {fetch: FetchResource; close: () => Promise<void>};
const MAX_RENDERED_BYTES = 5_000_000;
const MAX_BROWSER_REQUESTS = 100;
// How long to let a page keep talking before we snapshot it anyway.
const IDLE_TIMEOUT_MS = 10_000;
// And a last moment for a still-empty shell to paint something.
const PAINT_TIMEOUT_MS = 8_000;
// Content counts as settled once the visible text has not changed for this
// long and nothing on the page says it is still loading...
const SETTLE_QUIET_MS = 1_500;
const SETTLE_POLL_MS = 250;
// ...or, whatever the page is doing, once this much time has passed.
const SETTLE_MAX_MS = 15_000;

export type RouteDecision = "continue" | "abort" | "stub";

/**
 * What the rendering browser does with one request.
 *
 * Off-host requests never leave this machine -- that is the SSRF boundary and
 * it does not move. What changed is *how* they are refused. Aborting them made
 * the page's own code see a failed load, and codearc.net's tutorial pages
 * crash on a failed analytics or font-stylesheet load: all 127 of them were
 * stored as its "Oops! Something went wrong" screen. A real visitor, and
 * Googlebot, load those scripts fine, so the error screen was our artefact.
 *
 * So a sub-resource from another host is answered here with an empty 200: the
 * page carries on as if the script loaded and did nothing, and still no byte
 * goes out. A navigation off-host is still aborted -- stubbing it would swap
 * the page being observed for an empty one. Images, media and fonts on the
 * site's own host are skipped as before; they cost bandwidth and carry no
 * evidence.
 */
export function routeDecision(
  safe: boolean,
  resourceType: string,
  isNavigation: boolean,
): RouteDecision {
  if (!safe) return isNavigation ? "abort" : "stub";
  if (["image", "media", "font"].includes(resourceType)) return "abort";
  return "continue";
}

export function stubResponse(resourceType: string): {status: number; contentType: string; body: string} {
  const contentType =
    resourceType === "script" ? "application/javascript"
    : resourceType === "stylesheet" ? "text/css"
    : resourceType === "fetch" || resourceType === "xhr" ? "application/json"
    : "text/plain";
  return {status: 200, contentType, body: contentType === "application/json" ? "{}" : ""};
}

export function shouldRender(resource: FetchedResource, policy: RenderPolicy): boolean {
  if (!resource.contentType.includes("text/html") || policy === "never") return false;
  if (policy === "always") return true;
  const $ = load(resource.body);
  $("script,style,noscript,template,svg").remove();
  const textLength = $("body").text().replace(/\s+/g, " ").trim().length;
  const hasClientRuntime = resource.body.includes("<script");
  return textLength < 200 && hasClientRuntime;
}

/**
 * Wait for a client-rendered page to have fetched and shown what it is going to
 * show, then give up gracefully.
 *
 * `load` fires once subresources have arrived, but a client-rendered app mounts
 * after that, and only then fetches its content. Those are two separate events
 * and only the first one used to be waited for.
 *
 * Waiting for the body to hold *some* text cannot tell them apart. A shell is
 * not empty: the navigation, sidebar and breadcrumb mount immediately and clear
 * any small threshold on their own, so the condition was already true before a
 * single byte of content had been requested. On codearc.net that furniture is
 * ~340 characters against a 200 threshold, so all 424 of its URLs were captured
 * as the same shell -- and, because the breadcrumb differs per page, as a shell
 * just distinct enough to look like a real crawl. The evidence guard refused it
 * twice, which is the only reason none of it was ever analysed; the fetcher
 * should not have been producing it.
 *
 * So wait on the network instead. Idle means the app has finished asking for
 * what it needs, which is the thing the text threshold was trying to infer.
 */
export async function waitForRenderedContent(page: Page): Promise<void> {
  // Bounded and swallowed: a page that polls or holds a socket open never goes
  // idle, and for that one we take whatever has rendered by the deadline.
  await page.waitForLoadState("networkidle", {timeout: IDLE_TIMEOUT_MS}).catch(() => undefined);

  // The original guard, kept for the case it was written for: a root div still
  // empty after all that is worth a last moment to paint. Harmless when idle
  // already produced content -- it is then true on arrival.
  await page
    .waitForFunction(
      () => (document.body?.innerText ?? "").replace(/\s+/g, " ").trim().length > 200,
      undefined,
      {timeout: PAINT_TIMEOUT_MS},
    )
    .catch(() => undefined);

  await waitForSettledText(page);
}

/**
 * Wait until the page has stopped changing what it shows.
 *
 * Network idle is not enough on its own. On codearc.net the app's main bundle
 * arrives, the network pauses for the half-second it takes to execute, and
 * idle fires -- with the page showing "Loading challenge...". Only then does
 * it request the route's code and the challenge itself. Every one of 230
 * challenge pages was stored as that same 47-word loading screen, 0.5 seconds
 * before the real 249-word problem appeared, and the evidence guard refused
 * five crawls in a row for it (2026-09-06 to 2026-09-23).
 *
 * So also wait for the visible text to hold still, and for anything that
 * announces itself as loading -- `aria-busy` or the words
 * "loading..." -- to be gone. Bounded: a page that animates forever is taken
 * as it stands at the deadline.
 */
export async function waitForSettledText(
  page: Page,
  {quietMs = SETTLE_QUIET_MS, pollMs = SETTLE_POLL_MS, maxMs = SETTLE_MAX_MS} = {},
): Promise<void> {
  const deadline = Date.now() + maxMs;
  let last = "";
  let unchangedSince = Date.now();
  while (Date.now() < deadline) {
    let snapshot: {text: string; loading: boolean};
    try {
      snapshot = await page.evaluate(() => {
        // Digits are masked: a ticking clock or a live counter changes the
        // text every second and would otherwise hold every page to the cap
        // (codearc.net's challenge timer cost 15s a page).
        const text = (document.body?.innerText ?? "").replace(/\s+/g, " ").trim().replace(/\d/g, "0");
        const busy = document.querySelector('[aria-busy="true"]') !== null;
        return {text, loading: busy || /\bloading\b[^.!?]{0,40}(\.\.\.|…)/i.test(text)};
      });
    } catch {
      // A client-side redirect destroys the context mid-poll. Let the new
      // document start over rather than failing the page.
      last = "";
      unchangedSince = Date.now();
      await page.waitForTimeout(pollMs);
      continue;
    }
    if (snapshot.text !== last) {
      last = snapshot.text;
      unchangedSince = Date.now();
    } else if (!snapshot.loading && Date.now() - unchangedSince >= quietMs) {
      return;
    }
    await page.waitForTimeout(pollMs);
  }
}

export function createAdaptiveFetcher(
  allowedHosts: ReadonlySet<string>,
  policy: RenderPolicy,
): ManagedFetcher {
  const httpFetch = createHttpFetcher(allowedHosts);
  let browser: Browser | null = null;

  const render = async (resource: FetchedResource): Promise<FetchedResource> => {
    browser ??= await chromium.launch({headless: true});
    const context = await browser.newContext({
      acceptDownloads: false,
      serviceWorkers: "block",
      userAgent: "SEOAutopilotBot/0.1 (+https://example.invalid/bot)",
    });
    const page = await context.newPage();
    let requestCount = 0;
    try {
      page.on("dialog", dialog => { void dialog.dismiss(); });
      await page.route("**/*", async route => {
        const request = route.request();
        requestCount += 1;
        if (requestCount > MAX_BROWSER_REQUESTS) {
          await route.abort("blockedbyclient");
          return;
        }
        let safe = true;
        try {
          await assertSafeUrl(request.url(), allowedHosts);
        } catch {
          safe = false;
        }
        const decision = routeDecision(safe, request.resourceType(), request.isNavigationRequest());
        if (decision === "stub") {
          await route.fulfill(stubResponse(request.resourceType()));
        } else if (decision === "abort") {
          await route.abort("blockedbyclient");
        } else {
          await route.continue();
        }
      });
      const response = await page.goto(resource.finalUrl, {
        waitUntil: "load",
        timeout: 20_000,
      });
      await waitForRenderedContent(page);
      const finalUrl = (await assertSafeUrl(page.url(), allowedHosts)).toString();
      const body = await page.content();
      if (Buffer.byteLength(body, "utf8") > MAX_RENDERED_BYTES) {
        throw new Error("rendered_response_too_large");
      }
      return {
        requestedUrl: resource.requestedUrl,
        finalUrl,
        status: response?.status() ?? resource.status,
        contentType: response?.headers()["content-type"] ?? resource.contentType,
        body,
        rendered: true,
        serverBody: resource.body,
      };
    } finally {
      await context.close();
    }
  };

  return {
    fetch: async url => {
      const resource = await httpFetch(url);
      return shouldRender(resource, policy) ? render(resource) : resource;
    },
    close: async () => {
      await browser?.close();
      browser = null;
    },
  };
}
