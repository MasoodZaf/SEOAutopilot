/**
 * Which of the workspace's sites the dashboard is looking at.
 *
 * This replaces a frozen list of three hosts -- codearc.net, thecalchive.com,
 * wordkitapp.com -- that every action funnelled through. `resolvePortfolioSite`
 * failed closed to codearc.net for anything unrecognised, which was the right
 * shape of caution and the wrong mechanism: it meant a workspace that was not
 * ours could not select its own site at all. Typing your own host redirected
 * you, silently, to a site in somebody else's tenant.
 *
 * The safety property worth keeping is narrower than an allowlist. A host from
 * a query string is untrusted text that ends up in a URL and in hidden form
 * fields, so it has to be shaped like a hostname and nothing else. Whether the
 * caller may *see* that host is not a question this file can answer and never
 * was: the API answers it, from the tenant scope, on every request.
 */

// Deliberately strict: lowercase labels, dots and hyphens. No scheme, no path,
// no port, no userinfo, no percent-encoding -- all of which are how a value
// like this turns into a redirect somewhere else.
const HOST = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$/;

/**
 * A hostname, or the empty string when the input is not one.
 *
 * Empty rather than a default, because "no site selected" is a real state now
 * and substituting a site the caller did not ask for is what the old version
 * got wrong.
 */
export function safeHost(value) {
  if (typeof value !== "string") return "";
  const normalized = value.trim().toLowerCase();
  if (normalized.length > 253) return "";
  return HOST.test(normalized) ? normalized : "";
}

/** The dashboard URL for a host, carrying only parameters we recognise. */
export function pilotPath(host, params = {}) {
  const selected = safeHost(host);
  const query = new URLSearchParams();
  if (selected) query.set("site", selected);
  for (const [key, value] of Object.entries(params)) {
    if (typeof value === "string" && value.length > 0) query.set(key, value);
  }
  const search = query.toString();
  return search ? `/pilot?${search}` : "/pilot";
}

/**
 * The site the dashboard should show, given what was asked for.
 *
 * Falls back to the workspace's first site so that arriving at a bare /pilot
 * shows something, and returns undefined only when the workspace genuinely has
 * no sites -- which the page renders as an invitation to add one.
 */
export function selectSite(sites, requestedHost) {
  if (!Array.isArray(sites) || sites.length === 0) return undefined;
  const wanted = safeHost(requestedHost);
  if (wanted) {
    const match = sites.find((site) => site.normalized_host === wanted);
    if (match) return match;
  }
  return sites[0];
}
