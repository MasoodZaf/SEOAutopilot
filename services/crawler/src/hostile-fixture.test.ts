import assert from "node:assert/strict";
import {performance} from "node:perf_hooks";
import test from "node:test";

import {crawlSite} from "./crawl-engine.js";
import {createHostileFixture, HOSTILE_ORIGIN} from "./hostile-fixture.js";

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
