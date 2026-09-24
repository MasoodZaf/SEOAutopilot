/**
 * What one AI citation run says, question by question.
 *
 * Each tracked question was asked of each answer engine the workspace has a
 * key for. A cell is the verdict for one engine on one question. Kept out of
 * the page so every shape can be tested without rendering.
 */

export const ENGINES = [
  {provider: "anthropic", label: "Claude"},
  {provider: "openai", label: "ChatGPT"},
];

const FAILURES = {
  anthropic_key_rejected: "Claude key rejected",
  openai_key_rejected: "OpenAI key rejected",
  anthropic_key_forbidden: "Claude key lacks access",
  openai_key_forbidden: "OpenAI key lacks access",
  provider_rate_limited: "Rate limited",
  model_refused: "Declined to answer",
};

/** @typedef {{state: "cited" | "named" | "absent" | "failed" | "not_asked", label: string, detail: string}} Cell */

/**
 * @param {import("./model").AiCitationObservation} row
 * @returns {Cell}
 */
function cellFor(row) {
  if (row.status !== "answered") {
    return {state: "failed", label: "No answer", detail: FAILURES[row.error_code ?? ""] ?? "The provider returned no answer"};
  }
  const competitors = row.competitor_hosts.length ? ` Also cited: ${row.competitor_hosts.join(", ")}.` : "";
  if (row.site_cited) {
    return {state: "cited", label: `Cited #${row.own_citation_rank ?? "?"}`, detail: `Source #${row.own_citation_rank} of ${row.cited_hosts.length}.${competitors}`};
  }
  if (row.site_mentioned) {
    return {state: "named", label: "Named", detail: `Named in the answer but not linked.${competitors}`};
  }
  const top = row.cited_hosts.slice(0, 3).join(", ");
  // The hosts cited instead already include any competitor; naming it twice reads as a second finding.
  return {state: "absent", label: "Not cited", detail: top ? `Cited instead: ${top}.` : "The answer cited no sources."};
}

/**
 * @param {import("./model").AiCitationObservation[]} observations
 */
export function citationGrid(observations) {
  /** @type {Map<string, {promptId: string, prompt: string, cells: Record<string, Cell>, excerpt: string | null}>} */
  const rows = new Map();
  for (const row of observations) {
    const entry = rows.get(row.prompt_id) ?? {promptId: row.prompt_id, prompt: row.prompt, cells: {}, excerpt: null};
    entry.cells[row.provider] = cellFor(row);
    if (!entry.excerpt && row.answer_excerpt && (row.site_cited || row.site_mentioned)) entry.excerpt = row.answer_excerpt;
    rows.set(row.prompt_id, entry);
  }
  const asked = new Set(observations.map((row) => row.provider));
  const engines = ENGINES.filter((engine) => asked.has(engine.provider));
  for (const entry of rows.values()) {
    for (const engine of engines) {
      entry.cells[engine.provider] ??= {state: "not_asked", label: "Not asked", detail: "Over the monthly spend cap"};
    }
  }
  return {engines, rows: [...rows.values()]};
}

/** "$0.42", from integer micro-dollars. */
export function formatMicros(micros) {
  const dollars = Math.max(0, Number(micros) || 0) / 1_000_000;
  return `$${dollars.toFixed(dollars < 10 ? 2 : 0)}`;
}
