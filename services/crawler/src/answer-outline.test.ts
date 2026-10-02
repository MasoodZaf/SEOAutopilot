import assert from "node:assert/strict";
import test from "node:test";

import {load} from "cheerio";

import {MAX_OUTLINE_HEADINGS, buildAnswerOutline, isQuestionHeading, schemaQuestions} from "./answer-outline.js";

function outline(body: string, structuredData: unknown[] = []) {
  const $ = load(`<html><body>${body}</body></html>`);
  const read = (node: unknown) => $(node as Parameters<typeof $>[0]).text();
  return buildAnswerOutline($, read, $("body").text().replace(/\s+/g, " "), structuredData);
}

test("a question heading records the size and shape of the copy directly under it", () => {
  const result = outline(
    "<h2>How is compound interest calculated?</h2>"
    + "<p>Interest is added to the principal each period, and the next period earns interest on both.</p>"
    + "<p>A second paragraph that is not the direct answer.</p>"
    + "<h2>Formula</h2><ul><li>A = P(1 + r/n)^nt</li><li>Second item</li></ul>",
  );
  assert.deepEqual(result.headings, [
    {level: 2, text: "How is compound interest calculated?", question: true, answer_words: 16, answer_kind: "paragraph"},
    // The first list item is the first block: "A = P(1 + r/n)^nt".
    {level: 2, text: "Formula", question: false, answer_words: 5, answer_kind: "list"},
  ]);
  assert.equal(result.headings_truncated, false);
});

test("copy set in bare divs still counts as an answer", () => {
  const result = outline("<div><h3>What does it cost?</h3><div>It is free for everyone, with no account needed.</div></div>");
  assert.equal(result.headings[0]?.answer_words, 9);
  assert.equal(result.headings[0]?.answer_kind, "paragraph");
});

test("a line break inside a paragraph does not end the answer", () => {
  const result = outline("<h2>Why use it?</h2><p>Because it is fast<br>and it is accurate.</p>");
  assert.equal(result.headings[0]?.answer_words, 8);
});

test("a heading followed straight by another heading has no answer", () => {
  const result = outline("<h2>What is APR?</h2><h3>Details</h3><p>Some words here.</p><h1>Unrelated</h1><p>Not an answer to anything.</p>");
  assert.equal(result.headings[0]?.answer_kind, "none");
  assert.equal(result.headings[0]?.answer_words, 0);
  assert.equal(result.headings[1]?.answer_words, 3);
  assert.equal(result.headings.length, 2);
});

test("script and style text is not an answer", () => {
  const result = outline("<h2>How does it work?</h2><script>var copy='not words';</script><style>p{}</style><p>Two words</p>");
  assert.equal(result.headings[0]?.answer_words, 2);
});

test("only wh-words make a question without a question mark", () => {
  assert.equal(isQuestionHeading("How to calculate a mortgage"), true);
  assert.equal(isQuestionHeading("Do more with less"), false);
  assert.equal(isQuestionHeading("Do I need an account?"), true);
  assert.equal(isQuestionHeading("Pricing"), false);
});

test("headings beyond the cap are counted as truncated, not stored", () => {
  const many = Array.from({length: MAX_OUTLINE_HEADINGS + 5}, (_, i) => `<h2>Section ${i}</h2><p>Copy.</p>`).join("");
  const result = outline(many);
  assert.equal(result.headings.length, MAX_OUTLINE_HEADINGS);
  assert.equal(result.headings_truncated, true);
});

test("FAQ markup questions are checked against what the page shows", () => {
  const faq = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: [
      {"@type": "Question", name: "Is it free?", acceptedAnswer: {"@type": "Answer", text: "Yes."}},
      {"@type": "Question", name: "Can I export results?", acceptedAnswer: {"@type": "Answer", text: "Yes."}},
    ],
  };
  const result = outline("<h2>Is it <em>free</em>?</h2><p>Yes, always.</p>", [faq]);
  assert.deepEqual(result.faq_schema, {questions: 2, visible: 1});
});

test("FAQ questions are found inside a graph and under a list of types", () => {
  const graph = {"@graph": [{"@type": "WebPage"}, {"@type": ["FAQPage", "WebPage"], mainEntity: {"@type": "Question", name: "What is it?"}}]};
  assert.deepEqual(schemaQuestions([graph]), ["What is it?"]);
  assert.deepEqual(schemaQuestions([{"@type": "Organization", name: "Not a question"}]), []);
});
