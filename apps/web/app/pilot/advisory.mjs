const defaultAdvice = Object.freeze({
  correction: "Review the cited evidence and define the smallest reversible correction.",
  validation: "Re-crawl the affected URL and confirm the original finding is resolved without a new regression.",
});

const adviceByTitle = Object.freeze([
  Object.freeze({
    includes: "no H1 heading",
    correction: "Add one descriptive H1 that states the page's primary purpose and matches its visible content.",
    validation: "Confirm the rendered page has one clear primary heading and that title/H1 intent remains aligned.",
  }),
  Object.freeze({
    includes: "meta description is outside",
    correction: "Draft one unique 50–170 character summary grounded in the page's visible content; review it in a SERP preview.",
    validation: "Confirm the rendered description is present, unique, within the review range, and contains no unsupported claim.",
  }),
  Object.freeze({
    includes: "question headings are not followed by",
    correction: "Under each question heading, put a 15–100 word paragraph that answers it on its own, before any widget or detail. Draft direct answers writes them with your own AI key for you to check.",
    validation: "Re-crawl and confirm each question is followed by its answer, and that every figure in an answer was verified before it went live.",
  }),
  Object.freeze({
    includes: "no FAQPage, QAPage or HowTo markup",
    correction: "Add FAQPage JSON-LD whose questions and answers are exactly the ones visible on the page. Draft proposal builds it from the page's own headings and paragraphs, with no AI. It helps answer engines parse them; Google shows FAQ results for few sites.",
    validation: "Validate the markup and confirm every question in it appears in the rendered page text.",
  }),
  Object.freeze({
    includes: "questions in the page's FAQ markup do not appear",
    correction: "Remove the FAQ entries the page does not show, or add those questions and answers to the visible page. Markup must describe what readers see.",
    validation: "Re-crawl and confirm the markup's questions and the visible questions match one for one.",
  }),
  Object.freeze({
    includes: "fewer than 150 visible words",
    correction: "Review search intent first: consolidate or noindex a low-value page, or add genuinely useful original content. Do not pad word count.",
    validation: "Confirm the selected treatment matches page purpose and improves usefulness without duplicating another page.",
  }),
]);

export function advisoryFor(title) {
  if (typeof title !== "string") return defaultAdvice;
  return adviceByTitle.find((item) => title.includes(item.includes)) ?? defaultAdvice;
}

/** Whether an opportunity can be answered by drafting direct answers for its page. */
export function answersDraftable(title) {
  return typeof title === "string" && title.includes("question headings are not followed by");
}
