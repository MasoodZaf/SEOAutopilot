/**
 * Cookie names, and nothing else.
 *
 * The middleware runs on the edge runtime, which has no `node:crypto`. It only
 * needs to know whether a cookie is present, so it must be able to learn the
 * name without importing the module that opens the envelope — a build will
 * happily bundle that import and then fail on the first request instead.
 */
export const SESSION_COOKIE = "seo_autopilot_session";
export const OAUTH_STATE_COOKIE = "seo_autopilot_oauth";
