import assert from "node:assert/strict";
import test from "node:test";

import {describeDuration, describeOutcome, describeProgress} from "./crawl-state.mjs";

const NOW = "2026-09-23T05:20:00Z";

test("a running crawl shows pages fetched against pages expected", () => {
  const state = describeProgress(
    {
      status: "running",
      created_at: "2026-09-23T05:11:00Z",
      started_at: "2026-09-23T05:12:00Z",
      last_heartbeat_at: "2026-09-23T05:19:55Z",
      attempts: 1,
      progress: {phase: "fetching", fetched: 210, pending: 210, fetch_errors: 2},
    },
    NOW,
  );
  assert.equal(state.headline, "Fetching pages: 210 of about 420");
  assert.equal(state.percent, 43);
  assert.equal(state.stalled, false);
  assert.match(state.detail, /2 could not be fetched/);
  assert.match(state.detail, /8m 00s elapsed/);
});

test("saving fills the last stretch of the bar", () => {
  const state = describeProgress(
    {status: "running", created_at: NOW, started_at: NOW, last_heartbeat_at: NOW, progress: {phase: "saving", fetched: 400, saved: 200}},
    NOW,
  );
  assert.equal(state.percent, 93);
  assert.equal(state.headline, "Saving pages: 200 of 400");
});

test("a crawler that has gone quiet is called out, not shown as progress", () => {
  const state = describeProgress(
    {
      status: "running",
      created_at: "2026-09-23T05:00:00Z",
      started_at: "2026-09-23T05:01:00Z",
      last_heartbeat_at: "2026-09-23T05:15:00Z",
      attempts: 2,
      progress: {phase: "fetching", fetched: 10, pending: 90},
    },
    NOW,
  );
  assert.equal(state.stalled, true);
  assert.match(state.detail, /No word from the crawler for 5m 00s/);
  assert.match(state.detail, /attempt 2 of 3/);
});

test("a crawl from before progress existed still says something useful", () => {
  const state = describeProgress(
    {status: "running", created_at: NOW, started_at: "2026-09-23T05:18:00Z", last_heartbeat_at: NOW, progress: {}},
    NOW,
  );
  assert.equal(state.percent, null);
  assert.equal(state.headline, "Crawling");
});

test("a queued crawl says it is waiting", () => {
  const state = describeProgress({status: "queued", created_at: "2026-09-23T05:19:30Z", started_at: null}, NOW);
  assert.equal(state.headline, "Waiting for the crawler");
  assert.match(state.detail, /30s ago/);
});

test("content collapse is explained, with the evidence", () => {
  const outcome = describeOutcome({
    status: "failed",
    error_code: "content_collapse",
    result_summary: {pages_observed: 421, content_top_share: 0.5534, distinct_content_hashes: 15},
  });
  assert.equal(outcome.tone, "stop");
  assert.match(outcome.reason, /55% identical; 15 distinct versions across 421 pages/);
  assert.match(outcome.reason, /loading screen or a block page/);
});

test("a partial crawl names why it was partial", () => {
  const outcome = describeOutcome({
    status: "partial",
    error_code: null,
    result_summary: {pages_observed: 500, max_pages: 500, fetch_errors: 3},
  });
  assert.equal(outcome.tone, "warn");
  assert.match(outcome.reason, /reached the 500-page limit, and 3 pages could not be fetched/);
});

test("an unknown failure shows its code rather than hiding it", () => {
  const outcome = describeOutcome({status: "failed", error_code: "boom", result_summary: {}});
  assert.match(outcome.reason, /\(boom\)/);
});

test("durations read naturally", () => {
  assert.equal(describeDuration(52), "52s");
  assert.equal(describeDuration(245), "4m 05s");
  assert.equal(describeDuration(3780), "1h 03m");
});
