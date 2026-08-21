import {assertSafeUrl} from "./url-policy.js";
import type {FetchResource} from "./types.js";

const MAX_BYTES = 5_000_000;
const USER_AGENT = "SEOAutopilotBot/0.1 (+https://example.invalid/bot)";
const DEFAULT_DELAY_MS = 500;

export function createHttpFetcher(
  allowedHosts: ReadonlySet<string>,
  minimumDelayMs = DEFAULT_DELAY_MS,
): FetchResource {
  let nextRequestAt = 0;
  return async (initialUrl: URL) => {
    let current = initialUrl;
    for (let redirects = 0; redirects <= 5; redirects += 1) {
      await assertSafeUrl(current.toString(), allowedHosts);
      const waitMs = Math.max(0, nextRequestAt - Date.now());
      if (waitMs) await new Promise(resolve => setTimeout(resolve, waitMs));
      nextRequestAt = Date.now() + Math.max(0, minimumDelayMs);
      const response = await fetch(current, {
        redirect: "manual",
        signal: AbortSignal.timeout(15_000),
        headers: {"user-agent": USER_AGENT, accept: "text/html,application/xml,text/xml,text/plain"},
      });
      if (response.status >= 300 && response.status < 400) {
        const location = response.headers.get("location");
        if (!location) throw new Error("redirect_without_location");
        current = await assertSafeUrl(new URL(location, current).toString(), allowedHosts);
        continue;
      }
      const declaredLength = Number(response.headers.get("content-length") ?? 0);
      if (declaredLength > MAX_BYTES) throw new Error("response_too_large");
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.byteLength > MAX_BYTES) throw new Error("response_too_large");
      return {
        requestedUrl: initialUrl.toString(),
        finalUrl: current.toString(),
        status: response.status,
        contentType: response.headers.get("content-type") ?? "",
        body: new TextDecoder().decode(bytes),
        rendered: false,
      };
    }
    throw new Error("too_many_redirects");
  };
}
