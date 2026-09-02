import assert from "node:assert/strict";
import test from "node:test";

import {advisoryFor} from "./advisory.mjs";

test("H1 advice is concrete but remains a reviewable draft", () => {
  const advice = advisoryFor("The page has no H1 heading.");
  assert.match(advice.correction, /Add one descriptive H1/);
  assert.match(advice.validation, /rendered page/);
});

test("thin-content advice does not encourage word-count padding", () => {
  const advice = advisoryFor("The page has fewer than 150 visible words.");
  assert.match(advice.correction, /Do not pad word count/);
  assert.match(advice.validation, /page purpose/);
});

test("unknown findings fail closed to evidence review and reversible correction", () => {
  const advice = advisoryFor("Unexpected new rule");
  assert.match(advice.correction, /cited evidence/);
  assert.match(advice.correction, /reversible/);
});
