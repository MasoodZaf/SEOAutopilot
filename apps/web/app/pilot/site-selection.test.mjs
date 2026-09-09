import assert from "node:assert/strict";
import test from "node:test";

import {pilotPath, safeHost, selectSite} from "./site-selection.mjs";

const SITES = [
  {id: "1", name: "Acme", normalized_host: "acme.com", canonical_origin: "https://acme.com"},
  {id: "2", name: "Beta", normalized_host: "beta.io", canonical_origin: "https://beta.io"},
];

test("any real hostname is accepted, not just three of ours", () => {
  assert.equal(safeHost("acme.com"), "acme.com");
  assert.equal(safeHost("  Example.CO.UK "), "example.co.uk");
  assert.equal(safeHost("sub.domain.example.com"), "sub.domain.example.com");
});

test("anything that is not a bare hostname is refused rather than defaulted", () => {
  // The old version answered every one of these with "codearc.net", which is
  // how a tenant asking for its own site reached somebody else's.
  for (const input of [
    "https://acme.com",
    "acme.com/path",
    "acme.com:8080",
    "user@acme.com",
    "acme.com?x=1",
    "../etc",
    "localhost",
    "",
    null,
    undefined,
    123,
  ]) {
    assert.equal(safeHost(input), "", `expected ${String(input)} to be refused`);
  }
});

test("a path carries the host only when it is a real one", () => {
  assert.equal(pilotPath("acme.com", {crawl: "queued"}), "/pilot?site=acme.com&crawl=queued");
  assert.equal(pilotPath("https://evil.example"), "/pilot");
  assert.equal(pilotPath(null), "/pilot");
});

test("selection prefers what was asked for and falls back to the first site", () => {
  assert.equal(selectSite(SITES, "beta.io").id, "2");
  assert.equal(selectSite(SITES, "acme.com").id, "1");
  // Asked for a host this workspace does not have: show its own first site
  // rather than nothing, and never a site it does not own.
  assert.equal(selectSite(SITES, "somebody-elses.com").id, "1");
  assert.equal(selectSite(SITES, null).id, "1");
});

test("a workspace with no sites selects nothing", () => {
  assert.equal(selectSite([], "acme.com"), undefined);
  assert.equal(selectSite(undefined, "acme.com"), undefined);
});
