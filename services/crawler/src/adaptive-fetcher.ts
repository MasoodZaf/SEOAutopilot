import {load} from "cheerio";
import {chromium, type Browser} from "playwright";

import {createHttpFetcher} from "./http-fetcher.js";
import type {FetchedResource, FetchResource} from "./types.js";
import {assertSafeUrl} from "./url-policy.js";

export type RenderPolicy = "auto" | "always" | "never";
export type ManagedFetcher = {fetch: FetchResource; close: () => Promise<void>};
const MAX_RENDERED_BYTES = 5_000_000;
const MAX_BROWSER_REQUESTS = 100;

export function shouldRender(resource: FetchedResource, policy: RenderPolicy): boolean {
  if (!resource.contentType.includes("text/html") || policy === "never") return false;
  if (policy === "always") return true;
  const $ = load(resource.body);
  $("script,style,noscript,template,svg").remove();
  const textLength = $("body").text().replace(/\s+/g, " ").trim().length;
  const hasClientRuntime = resource.body.includes("<script");
  return textLength < 200 && hasClientRuntime;
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
        waitUntil: "domcontentloaded",
        timeout: 15_000,
      });
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
