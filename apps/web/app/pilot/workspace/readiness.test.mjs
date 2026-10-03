import assert from "node:assert/strict";
import test from "node:test";

import {readinessBreakdown} from "./readiness.mjs";

test("blocked answer crawlers are listed apart from refused training crawlers", () => {
  const view = readinessBreakdown({
    factors: {
      crawlable_indexable: {value: 0.9, measured: true},
      ai_crawler_access: {
        value: 0.5,
        measured: true,
        detail: {
          retrieval_blocked: [{token: "PerplexityBot", operator: "Perplexity", allowed_share: 0.25}],
          training_blocked: [{token: "GPTBot", operator: "OpenAI", allowed_share: 0}],
        },
      },
    },
    llms_txt: {status: "missing"},
  });
  assert.deepEqual(view.factors.map((row) => [row.key, row.percent]), [["crawlable_indexable", 90], ["ai_crawler_access", 50]]);
  assert.deepEqual(view.blocked, [{token: "PerplexityBot", operator: "Perplexity", refusedPercent: 75}]);
  assert.deepEqual(view.training, ["GPTBot"]);
  assert.equal(view.llmsTxt, "Not served");
  assert.equal(view.canProposeLlmsTxt, true);
});

test("an llms.txt that exists, even an invalid one, is never proposed over", () => {
  for (const status of ["present", "invalid", "unreachable", "disallowed"]) {
    assert.equal(readinessBreakdown({factors: {}, llms_txt: {status}}).canProposeLlmsTxt, false);
  }
});

test("an unmeasured factor reads as unmeasured, not as zero", () => {
  const view = readinessBreakdown({factors: {question_topic_coverage: {value: 0, measured: false}}});
  assert.equal(view.factors[0]?.percent, null);
});

test("a snapshot from before crawler access was measured renders without it", () => {
  const view = readinessBreakdown({factors: {entity_markup: {value: 1, measured: true}}});
  assert.deepEqual(view.blocked, []);
  assert.deepEqual(view.training, []);
  assert.equal(view.llmsTxt, null);
  assert.deepEqual(readinessBreakdown(null).factors, []);
});
