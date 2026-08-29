import assert from "node:assert/strict";
import test from "node:test";

import {pilotPath, portfolioSites, resolvePortfolioSite} from "./portfolio.mjs";

test("portfolio contains the three explicitly authorized design-partner sites", () => {
  assert.deepEqual(portfolioSites.map((site) => site.host), [
    "codearc.net",
    "thecalchive.com",
    "wordkitapp.com",
  ]);
});

test("unknown or malformed host input fails closed to the primary CodeArc pilot", () => {
  assert.equal(resolvePortfolioSite("evil.example").host, "codearc.net");
  assert.equal(resolvePortfolioSite(null).host, "codearc.net");
});

test("pilot paths retain only an allowlisted site host", () => {
  assert.equal(pilotPath("wordkitapp.com", {crawl: "queued"}), "/pilot?site=wordkitapp.com&crawl=queued");
  assert.equal(pilotPath("evil.example"), "/pilot?site=codearc.net");
});
