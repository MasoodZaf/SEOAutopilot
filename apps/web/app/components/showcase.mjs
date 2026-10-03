/**
 * Whether a visit came from the OryxenLabs showcase site.
 *
 * The showcase (oryxenlabs.com) has a card for this product. A visitor who
 * follows it gets a small way back; anyone who opens the app directly never
 * sees one. Kept out of the component so every shape can be tested.
 */

export const SHOWCASE_FLAG = "seoAutopilot.fromShowcase";

const bareHost = (url) => new URL(url).hostname.toLowerCase().replace(/^www\./, "");

/**
 * True when `referrer` is on the same host as `homeUrl`, ignoring `www.`.
 * An empty or malformed value on either side is false, never an error.
 *
 * @param {string | undefined} referrer
 * @param {string | undefined} homeUrl
 */
export function cameFromShowcase(referrer, homeUrl) {
  if (!referrer || !homeUrl) return false;
  try {
    return bareHost(referrer) === bareHost(homeUrl);
  } catch {
    return false;
  }
}
