import assert from "node:assert/strict";
import {performance} from "node:perf_hooks";
import test from "node:test";

import {crawlSite} from "./crawl-engine.js";
import {assessContentCollapse} from "./evidence-guard.js";
import {createHostileFixture, createShellWallFixture, HOSTILE_ORIGIN, SHELL_ORIGIN} from "./hostile-fixture.js";

test("500-page hostile fixture is bounded, contained, and reproducible", {timeout: 10_000}, async () => {
  const firstFixture = createHostileFixture();
  const firstStarted = performance.now();
  const first = await crawlSite(HOSTILE_ORIGIN, 500, firstFixture.fetchResource);
  const firstDuration = performance.now() - firstStarted;

  const secondFixture = createHostileFixture();
  const secondStarted = performance.now();
  const second = await crawlSite(HOSTILE_ORIGIN, 500, secondFixture.fetchResource);
  const secondDuration = performance.now() - secondStarted;

  assert.equal(first.observations.length, 500);
  assert.deepEqual(first.observations.map(page => page.normalizedUrl), firstFixture.expectedPageUrls);
  assert.deepEqual(first.observations, second.observations);
  assert.equal(first.skippedByRobots, 1);
  assert.equal(first.fetchErrors, 0);
  assert.equal(first.discoveryTruncated, true);
  assert.ok(firstDuration < 5_000, `first fixture run took ${firstDuration}ms`);
  assert.ok(secondDuration < 5_000, `second fixture run took ${secondDuration}ms`);

  assert.ok(!firstFixture.requestedUrls.some(url => url.includes("169.254.169.254")));
  assert.ok(!firstFixture.requestedUrls.some(url => url.includes("127.0.0.1")));
  assert.ok(!firstFixture.requestedUrls.some(url => url.includes("external.example")));
  assert.ok(!firstFixture.requestedUrls.some(url => url.includes("?")));
  assert.ok(!firstFixture.requestedUrls.includes(`${HOSTILE_ORIGIN}/private`));

  assert.equal(first.observations[1]?.canonicalUrl, "https://canonical-attacker.example/hijack");
  assert.equal(first.observations[2]?.canonicalUrl, null);
  assert.deepEqual(first.observations[3]?.structuredData, []);
  assert.deepEqual(first.observations[4]?.robotsDirectives, ["noindex", "follow"]);
  assert.equal(first.observations.some(page => page.linksTruncated), false);
  assert.ok(first.observations[6]?.contentHash);
});

test("a site that answers every URL with the same body is measured as collapsed", async () => {
  const fixture = createShellWallFixture();
  const result = await crawlSite(SHELL_ORIGIN, 200, fixture.fetchResource);

  // Everything a crawl normally reports says this went well, which is the
  // whole problem: without the content check there is nothing to fail on.
  assert.equal(result.fetchErrors, 0);
  assert.ok(result.observations.length > 100);
  assert.ok(result.observations.every(page => page.status === 200));

  const collapse = assessContentCollapse(result.observations);
  assert.equal(collapse.collapsed, true);
  assert.equal(collapse.distinctHashes, 1);
  assert.equal(collapse.topShare, 1);

  // And the findings such a crawl would produce, which is what made 1534 of them.
  assert.ok(result.observations.every(page => page.h1.length === 0));
  assert.equal(new Set(result.observations.map(page => page.title)).size, 1);
});

test("the 500-page hostile fixture is not mistaken for a shell", async () => {
  // The guard has to survive a large, adversarial, entirely legitimate crawl.
  // Every hostile page differs, so nothing here should trip it.
  const fixture = createHostileFixture();
  const result = await crawlSite(HOSTILE_ORIGIN, 500, fixture.fetchResource);

  const collapse = assessContentCollapse(result.observations);
  assert.equal(collapse.collapsed, false);
  assert.equal(collapse.distinctHashes, result.observations.length);
  assert.equal(collapse.distinctRatio, 1);
});
