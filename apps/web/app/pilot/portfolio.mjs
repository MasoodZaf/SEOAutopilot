export const portfolioSites = Object.freeze([
  Object.freeze({host: "codearc.net", name: "CodeArc", origin: "https://codearc.net"}),
  Object.freeze({host: "thecalchive.com", name: "TheCalcHive", origin: "https://thecalchive.com"}),
  Object.freeze({host: "wordkitapp.com", name: "WordKit", origin: "https://wordkitapp.com"}),
]);

export function resolvePortfolioSite(host) {
  const normalized = typeof host === "string" ? host.trim().toLowerCase() : "";
  return portfolioSites.find((site) => site.host === normalized) ?? portfolioSites[0];
}

export function pilotPath(host, params = {}) {
  const selected = resolvePortfolioSite(host);
  const query = new URLSearchParams({site: selected.host});
  for (const [key, value] of Object.entries(params)) {
    if (typeof value === "string" && value.length > 0) query.set(key, value);
  }
  return `/pilot?${query.toString()}`;
}
