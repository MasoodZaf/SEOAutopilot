import assert from "node:assert/strict";
import test from "node:test";

import {citationGrid, formatMicros} from "./citations.mjs";

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
