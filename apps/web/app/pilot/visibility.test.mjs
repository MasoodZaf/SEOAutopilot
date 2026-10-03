import assert from "node:assert/strict";
import test from "node:test";

import {bars, formatChange, scorecard} from "./visibility.mjs";

const week = (i, extra = {}) => ({
  week_start: `2026-08-${String(i + 1).padStart(2, "0")}`, clicks: 1, impressions: 100,
  branded_clicks: 0, branded_impressions: 10, zero_click_queries: 2, zero_click_impressions: 30,
  direct_sessions: 5, sessions: 20, ...extra,
});

const trend = (overrides = {}) => ({
  weeks: Array.from({length: 8}, (_, i) => week(i, i >= 4 ? {branded_impressions: 15, direct_sessions: 10} : {})),
  citation_runs: [
    {finished_at: "2026-09-01", answers: 4, named: 1, cited: 0, competitor_cited: 2},
    {finished_at: "2026-09-08", answers: 4, named: 2, cited: 1, competitor_cited: 1},
  ],
  branded_available: true, search_connected: true, analytics_connected: true, sparse: false,
  ...overrides,
});

test("the latest four weeks are compared with the four before", () => {
  const card = scorecard(trend());
  assert.equal(card.branded.current, 60);
  assert.equal(card.branded.change, 0.5);
  assert.equal(card.direct.change, 1);
  assert.equal(card.zeroClick.current, 0.3);
  assert.equal(card.zeroClick.queries, 8);
});

test("AI answers compare the latest run with the one before, in points", () => {
  const card = scorecard(trend());
  assert.equal(card.ai.current, 0.5);
  assert.equal(card.ai.change, 0.25);
  assert.equal(card.ai.cited, 0.25);
  assert.equal(formatChange(card.ai.change, {points: true}), "+25 pts");
});

test("a source that is not connected is null, not zero", () => {
  const card = scorecard(trend({analytics_connected: false, branded_available: false, citation_runs: []}));
  assert.equal(card.direct.current, null);
  assert.equal(card.branded.current, null);
  assert.equal(card.ai.current, null);
  assert.equal(card.ai.runs, 0);
});

test("growth from nothing has no percentage", () => {
  const weeks = Array.from({length: 8}, (_, i) => week(i, {branded_impressions: i >= 4 ? 3 : 0}));
  assert.equal(scorecard(trend({weeks})).branded.change, null);
  assert.equal(formatChange(null), "");
  assert.equal(formatChange(-0.084), "−8%");
});

test("bars scale to the tallest and leave gaps for missing weeks", () => {
  assert.deepEqual(bars([2, null, 4, 0]), [0.5, null, 1, 0]);
  assert.deepEqual(bars([0, 0]), [0, 0]);
});
