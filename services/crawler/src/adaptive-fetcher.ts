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
        try {
          await assertSafeUrl(request.url(), allowedHosts);
        } catch {
          await route.abort("blockedbyclient");
          return;
        }
        if (["image", "media", "font"].includes(request.resourceType())) {
          await route.abort("blockedbyclient");
          return;
        }
        await route.continue();
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
