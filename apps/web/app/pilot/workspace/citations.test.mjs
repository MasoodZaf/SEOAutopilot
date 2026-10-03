import assert from "node:assert/strict";
import test from "node:test";

import {citationGrid, citationShare, formatMicros} from "./citations.mjs";

const base = {
  model: "m", status: "answered", error_code: null, site_cited: false, site_mentioned: false,
  own_citation_rank: null, cited_hosts: [], own_urls: [], competitor_hosts: [], answer_excerpt: null, observed_at: "2026-09-24T06:00:00Z",
};

test("each question is a row with one verdict per engine that was asked", () => {
  const grid = citationGrid([
    {...base, prompt_id: "p1", prompt: "How is EMI calculated?", provider: "anthropic", site_cited: true, own_citation_rank: 2, cited_hosts: ["bank.example", "calc.example"], competitor_hosts: ["bank.example"], answer_excerpt: "Calc explains it."},
    {...base, prompt_id: "p1", prompt: "How is EMI calculated?", provider: "openai", cited_hosts: ["bank.example", "wiki.example"]},
  ]);
  assert.deepEqual(grid.engines.map((engine) => engine.label), ["Claude", "ChatGPT"]);
  const [row] = grid.rows;
  assert.equal(row?.cells.anthropic?.label, "Cited #2");
  assert.match(row?.cells.anthropic?.detail ?? "", /Also cited: bank\.example/);
  assert.equal(row?.cells.openai?.state, "absent");
  assert.equal(row?.cells.openai?.detail, "Cited instead: bank.example, wiki.example.");
  assert.equal(row?.excerpt, "Calc explains it.");
});

test("a named-but-unlinked answer and a failed one read differently", () => {
  const grid = citationGrid([
    {...base, prompt_id: "p1", prompt: "q", provider: "anthropic", site_mentioned: true},
    {...base, prompt_id: "p1", prompt: "q", provider: "openai", status: "failed", error_code: "openai_key_rejected"},
  ]);
  assert.equal(grid.rows[0]?.cells.anthropic?.state, "named");
  assert.deepEqual([grid.rows[0]?.cells.openai?.state, grid.rows[0]?.cells.openai?.detail], ["failed", "OpenAI key rejected"]);
});

test("an engine asked for some questions but not others marks the gap", () => {
  const grid = citationGrid([
    {...base, prompt_id: "p1", prompt: "a", provider: "anthropic"},
    {...base, prompt_id: "p1", prompt: "a", provider: "openai"},
    {...base, prompt_id: "p2", prompt: "b", provider: "anthropic"},
  ]);
  assert.equal(grid.rows[1]?.cells.openai?.state, "not_asked");
});

test("an engine with no key is not a column", () => {
  const grid = citationGrid([{...base, prompt_id: "p1", prompt: "a", provider: "openai"}]);
  assert.deepEqual(grid.engines.map((engine) => engine.provider), ["openai"]);
});

test("spend reads in dollars", () => {
  assert.equal(formatMicros(420_000), "$0.42");
  assert.equal(formatMicros(12_300_000), "$12");
});

test("citation share counts each host once per answer and tags whose it is", () => {
  const share = citationShare([
    {...base, prompt_id: "p1", prompt: "q1", provider: "anthropic", site_cited: true, own_citation_rank: 2, cited_hosts: ["bank.example", "www.calc.example", "bank.example"], competitor_hosts: ["bank.example"]},
    {...base, prompt_id: "p1", prompt: "q1", provider: "openai", cited_hosts: ["bank.example", "wiki.example"]},
    {...base, prompt_id: "p2", prompt: "q2", provider: "anthropic", cited_hosts: ["wiki.example"]},
    {...base, prompt_id: "p2", prompt: "q2", provider: "openai", status: "failed", error_code: "provider_rate_limited", cited_hosts: ["bank.example"]},
  ], "calc.example");
  assert.equal(share.answers, 3);
  assert.equal(share.cited, 1);
  assert.deepEqual(share.hosts, [
    {host: "bank.example", kind: "competitor", answers: 2},
    {host: "wiki.example", kind: "other", answers: 2},
    {host: "calc.example", kind: "yours", answers: 1},
  ]);
});

test("the site stays on the share table when it falls outside the top", () => {
  const rows = ["a", "b", "c"].map((host) => ({...base, prompt_id: host, prompt: host, provider: "anthropic", cited_hosts: [`${host}.example`, `${host}2.example`]}));
  rows.push({...base, prompt_id: "z", prompt: "z", provider: "openai", site_cited: true, own_citation_rank: 1, cited_hosts: ["calc.example"]});
  const share = citationShare(rows, "calc.example", 2);
  assert.equal(share.hosts.length, 3);
  assert.deepEqual(share.hosts.at(-1), {host: "calc.example", kind: "yours", answers: 1});
});

test("a run with no answers has an empty share", () => {
  assert.deepEqual(citationShare([], "calc.example"), {answers: 0, hosts: [], cited: 0});
});

test("cited sources are sorted by what reaching them takes", async () => {
  const {sourceKind, SOURCE_ADVICE} = await import("./citations.mjs");
  assert.equal(sourceKind("www.reddit.com"), "forum");
  assert.equal(sourceKind("en.wikipedia.org"), "reference");
  assert.equal(sourceKind("youtube.com"), "video");
  assert.equal(sourceKind("g2.com"), "review");
  // A lookalike is not the site it imitates.
  assert.equal(sourceKind("notreddit.com"), "other");
  assert.equal(sourceKind(""), "other");
  for (const kind of ["forum", "reference", "video", "review", "other", "yours", "competitor"]) {
    assert.ok(SOURCE_ADVICE[kind]);
  }
});

test("the share table tags a forum but keeps a tracked competitor a competitor", () => {
  const share = citationShare([
    {...base, prompt_id: "p1", prompt: "q1", provider: "openai", cited_hosts: ["www.reddit.com", "g2.com"], competitor_hosts: ["g2.com"]},
  ], "calc.example");
  assert.deepEqual(share.hosts.map((row) => [row.host, row.kind]), [["g2.com", "competitor"], ["reddit.com", "forum"]]);
});
