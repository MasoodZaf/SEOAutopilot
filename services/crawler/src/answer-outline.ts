import type {CheerioAPI} from "cheerio";

/**
 * How a page is laid out for an answer engine to lift an answer from it.
 *
 * Answer engines quote a passage, and the passage they can use is the one that
 * sits directly under the question it answers. So for each H2/H3 this records
 * the heading and the size and shape of the first block of copy beneath it --
 * never the copy itself. Headings are stored as H1s already are; body text is
 * not stored at all.
 *
 * The walk is over text nodes in document order rather than over `<p>`
 * elements, because plenty of sites set their copy in bare `<div>`s and would
 * otherwise read as having no answers anywhere.
 */
export type AnswerKind = "paragraph" | "list" | "table" | "none";

export type OutlineHeading = {
  level: 2 | 3;
  text: string;
  question: boolean;
  /** Words in the first block of copy after the heading, before the next heading. */
  answer_words: number;
  answer_kind: AnswerKind;
};

export type AnswerOutline = {
  schema_version: 1;
  headings: OutlineHeading[];
  headings_truncated: boolean;
  /** Questions declared in FAQPage/QAPage JSON-LD, and how many of them the page shows. */
  faq_schema: {questions: number; visible: number};
};

export const MAX_OUTLINE_HEADINGS = 40;
const MAX_HEADING_CHARS = 200;
const MAX_SCHEMA_QUESTIONS = 50;

// Kept in step with QUESTION_WORDS in services/api/app/services/ai_citations.py.
// Only the wh-words make a heading a question on their own: "Do more with
// less" starts like a question and is not one, so an auxiliary verb needs the
// question mark as well.
const WH_WORDS = new Set(["how", "what", "which", "why", "when", "where", "who"]);

// The crawler's word boundaries, less <br>: a line break inside a paragraph
// separates words, not answers. Reaching one of these ends a block of copy.
const BLOCK_TAGS = new Set([
  "p", "div", "li", "tr", "td", "th", "section", "article", "header", "footer", "nav",
  "aside", "blockquote", "pre", "figure", "figcaption", "option", "dt", "dd", "hr", "form", "table",
]);
const LIST_TAGS = new Set(["ul", "ol", "dl", "li", "dt", "dd"]);
const TABLE_TAGS = new Set(["table", "tr", "td", "th"]);
const SKIPPED_TAGS = new Set(["script", "style", "noscript", "template"]);
const HEADING = /^h([1-6])$/;

/** The parts of a parsed node this walk reads; cheerio's nodes satisfy it. */
type DomNode = {type: string; name?: string; data?: string; children?: DomNode[]};

type Section = {heading: OutlineHeading | null; words: number; kind: AnswerKind; closed: boolean};

const clean = (value: string): string => value.replace(/\s+/g, " ").trim();

export function isQuestionHeading(text: string): boolean {
  const first = text.split(" ", 1)[0]?.toLowerCase().replace(/[^a-z]/g, "") ?? "";
  return text.endsWith("?") || WH_WORDS.has(first);
}

export function buildAnswerOutline(
  $: CheerioAPI,
  readHeading: (node: unknown) => string,
  visibleText: string,
  structuredData: readonly unknown[],
): AnswerOutline {
  const headings: OutlineHeading[] = [];
  let truncated = false;
  // Copy before the first heading belongs to no question.
  let section: Section = {heading: null, words: 0, kind: "none", closed: true};

  const settle = (): void => {
    if (section.heading) {
      section.heading.answer_words = section.words;
      section.heading.answer_kind = section.kind;
    }
  };

  const visit = (node: DomNode, context: "paragraph" | "list" | "table"): void => {
    if (node.type === "text") {
      const words = clean(node.data ?? "");
      if (!words || section.closed) return;
      if (section.words === 0) section.kind = context;
      section.words += words.split(" ").length;
      return;
    }
    // Cheerio types <script> and <style> as their own node kinds, not as tags.
    if (SKIPPED_TAGS.has(node.type)) return;
    if (node.type !== "tag" || !node.name) {
      for (const child of node.children ?? []) visit(child, context);
      return;
    }
    const name = node.name.toLowerCase();
    if (SKIPPED_TAGS.has(name)) return;
    const level = HEADING.exec(name);
    if (level) {
      settle();
      const depth = Number(level[1]);
      if (depth !== 2 && depth !== 3) {
        // An H1 or a minor heading ends the answer above it but asks nothing.
        section = {heading: null, words: 0, kind: "none", closed: true};
        return;
      }
      const text = clean(readHeading(node)).slice(0, MAX_HEADING_CHARS);
      if (!text) {
        section = {heading: null, words: 0, kind: "none", closed: true};
        return;
      }
      if (headings.length >= MAX_OUTLINE_HEADINGS) {
        truncated = true;
        section = {heading: null, words: 0, kind: "none", closed: true};
        return;
      }
      const heading: OutlineHeading = {
        level: depth as 2 | 3, text, question: isQuestionHeading(text), answer_words: 0, answer_kind: "none",
      };
      headings.push(heading);
      section = {heading, words: 0, kind: "none", closed: false};
      return;
    }
    const nested = LIST_TAGS.has(name) ? "list" : TABLE_TAGS.has(name) ? "table" : context;
    const boundary = BLOCK_TAGS.has(name);
    if (boundary && section.words > 0) section.closed = true;
    for (const child of node.children ?? []) visit(child, nested);
    if (boundary && section.words > 0) section.closed = true;
  };

  const body = $("body").get(0) as unknown as DomNode | undefined;
  if (body) visit(body, "paragraph");
  settle();

  return {
    schema_version: 1,
    headings,
    headings_truncated: truncated,
    faq_schema: faqSchemaVisibility(structuredData, visibleText),
  };
}

const comparable = (value: string): string =>
  clean(value.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " "));

function typeList(value: Record<string, unknown>): string[] {
  const raw = value["@type"];
  return (Array.isArray(raw) ? raw : [raw]).filter((item): item is string => typeof item === "string");
}

/** The question names a page declares in FAQPage or QAPage markup. */
export function schemaQuestions(structuredData: readonly unknown[]): string[] {
  const found: string[] = [];
  const visit = (value: unknown, depth: number): void => {
    if (depth > 6 || found.length >= MAX_SCHEMA_QUESTIONS || !value || typeof value !== "object") return;
    if (Array.isArray(value)) {
      for (const item of value) visit(item, depth + 1);
      return;
    }
    const record = value as Record<string, unknown>;
    const types = typeList(record);
    if (types.includes("FAQPage") || types.includes("QAPage")) {
      const entities = Array.isArray(record.mainEntity) ? record.mainEntity : [record.mainEntity];
      for (const entity of entities) {
        if (found.length >= MAX_SCHEMA_QUESTIONS) break;
        const name = entity && typeof entity === "object" ? (entity as Record<string, unknown>).name : undefined;
        if (typeof name === "string" && clean(name)) found.push(clean(name));
      }
      return;
    }
    visit(record["@graph"], depth + 1);
  };
  for (const item of structuredData) visit(item, 0);
  return found;
}

function faqSchemaVisibility(structuredData: readonly unknown[], visibleText: string): AnswerOutline["faq_schema"] {
  const questions = schemaQuestions(structuredData);
  const page = ` ${comparable(visibleText)} `;
  const visible = questions.filter(question => {
    const wanted = comparable(question);
    return wanted.length > 0 && page.includes(` ${wanted} `);
  }).length;
  return {questions: questions.length, visible};
}
