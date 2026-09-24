/**
 * What an answer-engine readiness snapshot says, factor by factor.
 *
 * The snapshot's `factors_json` is written by the worker; this turns it into
 * rows a reader can act on, and keeps the llms.txt and training-bot lines
 * apart from the score they do not contribute to. Kept out of the page so
 * every shape -- including snapshots written before a factor existed -- can
 * be tested without rendering.
 */

const LABELS = {
  crawlable_indexable: "Crawlable and indexable pages",
  entity_markup: "Entity markup",
  question_answer_markup: "FAQ, Q&A and HowTo markup",
  question_topic_coverage: "Questions answered on a page",
  ai_crawler_access: "Answer-engine crawler access",
};

const LLMS_TXT = {
  present: "Served",
  missing: "Not served",
  invalid: "Served, but not a valid llms.txt",
  unreachable: "Could not be fetched",
  disallowed: "Closed to crawlers by robots.txt",
};

const record = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : {});
const list = (value) => (Array.isArray(value) ? value.map(record) : []);

/**
 * @param {Record<string, unknown> | null | undefined} factorsJson
 * @returns {{
 *   factors: Array<{key: string, label: string, percent: number | null}>,
 *   blocked: Array<{token: string, operator: string, refusedPercent: number}>,
 *   training: string[],
 *   llmsTxt: string | null,
 *   canProposeLlmsTxt: boolean,
 * }}
 */
export function readinessBreakdown(factorsJson) {
  const factors = record(record(factorsJson).factors);
  const rows = Object.keys(LABELS)
    .filter((key) => key in factors)
    .map((key) => {
      const factor = record(factors[key]);
      const value = Number(factor.value);
      return {
        key,
        label: LABELS[key],
        percent: factor.measured === true && Number.isFinite(value) ? Math.round(value * 100) : null,
      };
    });
  const detail = record(record(factors.ai_crawler_access).detail);
  const blocked = list(detail.retrieval_blocked).map((row) => ({
    token: String(row.token ?? ""),
    operator: String(row.operator ?? ""),
    refusedPercent: Math.round((1 - Number(row.allowed_share ?? 0)) * 100),
  }));
  const training = list(detail.training_blocked).map((row) => String(row.token ?? ""));
  const status = record(record(factorsJson).llms_txt).status;
  return {
    factors: rows,
    blocked,
    training,
    llmsTxt: typeof status === "string" ? (LLMS_TXT[status] ?? null) : null,
    // Only a file the crawl saw missing is proposed; one that exists, even an
    // invalid one, is the site owner's to edit.
    canProposeLlmsTxt: status === "missing",
  };
}
