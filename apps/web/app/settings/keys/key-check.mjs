/**
 * The stored-key check, carried through a redirect and read back as advice.
 *
 * The result travels in the query string as `model:status` pairs, so the
 * page is re-read on parse: anything that is not a short model name and a
 * known status is dropped rather than shown.
 */

const STATUSES = new Set([
  "available",
  "model_unavailable",
  "key_rejected",
  "not_permitted",
  "rate_limited",
  "unreachable",
]);
const PAIR = /^([a-z0-9][a-z0-9.-]{0,59}):([a-z_]{1,30})$/;

/** @param {{model: string, status: string}[]} models */
export function encodeCheck(models) {
  return models
    .filter((item) => PAIR.test(`${item.model}:${item.status}`) && STATUSES.has(item.status))
    .map((item) => `${item.model}:${item.status}`)
    .join(",");
}

/** @param {string | undefined} value */
export function decodeCheck(value) {
  if (!value) return [];
  return value
    .split(",")
    .slice(0, 4)
    .map((pair) => PAIR.exec(pair))
    .filter((match) => match && STATUSES.has(match[2]))
    .map((match) => ({model: match[1], status: match[2]}));
}

const CONSOLE = {openai_api_key: "platform.openai.com", anthropic_api_key: "console.anthropic.com"};
const NAME = {openai_api_key: "OpenAI", anthropic_api_key: "Anthropic"};

/**
 * One sentence of what to do, and whether it is good news.
 * @param {string} provider
 * @param {{model: string, status: string}[]} models
 * @returns {{tone: "good" | "warn" | "stop", text: string} | null}
 */
export function explainCheck(provider, models) {
  const name = NAME[provider];
  if (!name || models.length === 0) return null;
  const [primary, ...others] = models;
  const fallback = others.find((item) => item.model !== primary.model && item.status === "available" && item.model.startsWith("gpt-4"));
  if (primary.status === "available") {
    const better = others.filter((item) => item.model.startsWith("gpt-5") && item.model !== primary.model);
    const note = better.length
      ? better[0].status === "available"
        ? ` The key could also use ${better[0].model}; this deployment is set to ${primary.model}.`
        : ` It cannot use ${better[0].model} yet.`
      : "";
    return {tone: "good", text: `${name} accepts this key for ${primary.model}, so drafts are written with ${primary.model}.${note}`};
  }
  if (primary.status === "model_unavailable") {
    const steps =
      provider === "openai_api_key"
        ? ` In ${CONSOLE[provider]}, check Settings → Organization → General → Verifications says Verified (access can take a while to switch on after verifying), and that Settings → Project → Limits does not block ${primary.model} for the key's project.`
        : ` Check in ${CONSOLE[provider]} that your account has access to ${primary.model}.`;
    const lead =
      fallback
        ? `The key works, but ${name} won't let it use ${primary.model}, so drafts fall back to ${fallback.model}.`
        : `The key works, but ${name} won't let it use ${primary.model}.`;
    return {tone: "warn", text: lead + steps};
  }
  if (primary.status === "key_rejected") {
    return {tone: "stop", text: `${name} rejects this key: it was revoked, mistyped, or its project was deleted. Create a new key at ${CONSOLE[provider]} and replace it here.`};
  }
  if (primary.status === "not_permitted") {
    return {tone: "stop", text: `This key is restricted. In ${CONSOLE[provider]}, give it access to model calls, or replace it with an unrestricted key.`};
  }
  if (primary.status === "rate_limited") {
    return {tone: "warn", text: `${name} is refusing calls on this key for now: rate-limited, or out of credit. Check billing at ${CONSOLE[provider]}, then check again.`};
  }
  return {tone: "warn", text: `${name} could not be reached. Nothing is wrong with the key as far as we know; check again in a minute.`};
}
