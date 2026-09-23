import assert from "node:assert/strict";
import test from "node:test";

import {assessContentCollapse} from "./evidence-guard.js";
import type {PageObservation} from "./types.js";

const observation = (contentHash: string, status = 200): PageObservation => ({
  normalizedUrl: `https://example.com/${contentHash}`,
  finalUrl: `https://example.com/${contentHash}`,
  status,
  title: "Example",
  metaDescription: null,
  h1: [],
  wordCount: 30,
  serverWordCount: null,
  contentHash,
  rendered: false,
  canonicalUrl: null,
  robotsDirectives: [],
  structuredData: [],
  links: [],
  linkCountTotal: 0,
  linksTruncated: false,
});

/** Distinct body per page: the shape every real crawl in the fleet has. */
const distinct = (count: number): PageObservation[] =>
  Array.from({length: count}, (_, index) => observation(`hash-${index}`));

test("a crawl whose pages all share one body is collapsed", () => {
  const result = assessContentCollapse(Array.from({length: 500}, () => observation("shell")));
  assert.equal(result.collapsed, true);
  assert.equal(result.distinctHashes, 1);
  assert.equal(result.topShare, 1);
});

test("a handful of shells across hundreds of pages is still collapsed", () => {
  // The measured CodeArc shape: 500 observations over 5 distinct bodies, the
  // largest covering 99.2% of them.
  const pages = [...Array.from({length: 496}, () => observation("shell")), ...distinct(4)];
  assert.equal(assessContentCollapse(pages).collapsed, true);
});

test("many distinct shells still collapse on the distinct ratio", () => {
  // No single body dominates, but 500 pages carrying 20 bodies means 96% of
  // them are copies -- evidence just as unusable as one wall.
  const pages = Array.from({length: 500}, (_, index) => observation(`shell-${index % 20}`));
  const result = assessContentCollapse(pages);
  assert.equal(result.topShare < 0.8, true);
  assert.equal(result.collapsed, true);
});

test("a site with a distinct body per page passes", () => {
  const result = assessContentCollapse(distinct(32));
  assert.equal(result.collapsed, false);
  assert.equal(result.distinctRatio, 1);
});

test("small crawls are exempt, since few pages prove nothing", () => {
  assert.equal(assessContentCollapse([observation("shell"), observation("shell")]).collapsed, false);
});

test("shared error bodies do not trip the guard", () => {
  // A crawl that is mostly errors is already reported through fetch_errors;
  // 404 pages legitimately share a body and must not read as a shell.
  const pages = [...Array.from({length: 400}, () => observation("not-found", 404)), ...distinct(20)];
  const result = assessContentCollapse(pages);
  assert.equal(result.observations, 20);
  assert.equal(result.collapsed, false);
});

test("a crawl with no successful fetches is not called collapsed", () => {
  const result = assessContentCollapse(Array.from({length: 50}, () => observation("error", 500)));
  assert.equal(result.observations, 0);
  assert.equal(result.collapsed, false);
});
