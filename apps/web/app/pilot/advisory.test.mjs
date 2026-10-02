import assert from "node:assert/strict";
import test from "node:test";

import {advisoryFor, answersDraftable} from "./advisory.mjs";

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

test("unanswered question headings get answer advice and a drafting action", () => {
  const title = "2 of 3 question headings are not followed by a 15–100 word answer an AI engine could quote.";
  assert.match(advisoryFor(title).correction, /answers it on its own/);
  assert.equal(answersDraftable(title), true);
  assert.equal(answersDraftable("The page has no H1 heading."), false);
});

test("FAQ markup advice never promises Google rich results", () => {
  const advice = advisoryFor("2 questions are answered on the page but no FAQPage, QAPage or HowTo markup declares them.");
  assert.match(advice.correction, /few sites/);
  assert.match(advisoryFor("3 of 5 questions in the page's FAQ markup do not appear in its visible text.").correction, /what readers see/);
});
