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
    includes: "fewer than 150 visible words",
    correction: "Review search intent first: consolidate or noindex a low-value page, or add genuinely useful original content. Do not pad word count.",
    validation: "Confirm the selected treatment matches page purpose and improves usefulness without duplicating another page.",
  }),
]);

export function advisoryFor(title) {
  if (typeof title !== "string") return defaultAdvice;
  return adviceByTitle.find((item) => title.includes(item.includes)) ?? defaultAdvice;
}
