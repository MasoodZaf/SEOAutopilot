/**
 * What a connector's row means to the person looking at it.
 *
 * Kept apart from the page so it can be tested without rendering: the page used
 * to print the raw status with underscores replaced, which is how four dead
 * Google grants sat on it for five days reading "reauthorization required" in
 * the same grey as everything else.
 */

/** @typedef {"neutral" | "accent" | "good" | "warn" | "stop"} Tone */

/**
 * @param {string | undefined} status
 * @returns {{label: string, tone: Tone, broken: boolean}}
 */
export function describeStatus(status) {
  switch (status) {
    case "active":
      return {label: "Connected", tone: "good", broken: false};
    case "reauthorization_required":
      return {label: "Needs reconnecting", tone: "stop", broken: true};
    case "error":
      return {label: "Failing", tone: "stop", broken: true};
    case "pending_authorization":
      return {label: "Not finished", tone: "warn", broken: false};
    case "revoked":
      return {label: "Disconnected", tone: "neutral", broken: false};
    default:
      return {label: "Not connected", tone: "neutral", broken: false};
  }
}

const REASONS = {
  authorization_required:
    "The provider stopped accepting this connection. Someone has to connect it again.",
  token_refresh_unavailable:
    "The provider could not be reached at the last check. It is retried automatically.",
  token_refresh_timeout:
    "The provider timed out at the last check. It is retried automatically.",
  token_refresh_not_configured:
    "This workspace has no Google OAuth client to renew the connection with. Add one under Keys.",
  lease_expired: "The last sync was abandoned by the worker and did not finish.",
};

/**
 * @param {string | null | undefined} code
 * @returns {string | null}
 */
export function describeError(code) {
  if (!code) return null;
  return REASONS[/** @type {keyof typeof REASONS} */ (code)] ?? `The last attempt failed: ${code}.`;
}

const DAY = 86_400_000;

/**
 * A date as a person reads it, with how far away it is.
 *
 * @param {string | null | undefined} iso
 * @param {Date} [now]
 * @returns {string | null}
 */
export function describeDate(iso, now = new Date()) {
  if (!iso) return null;
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return null;
  const date = when.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
  const days = Math.round((when.getTime() - now.getTime()) / DAY);
  const relative =
    days === 0 ? "today" : days > 0 ? `in ${plural(days, "day")}` : `${plural(-days, "day")} ago`;
  return `${date} (${relative})`;
}

/**
 * When the grant stops being honoured, said the way it matters.
 *
 * @param {string | null | undefined} iso
 * @param {Date} [now]
 * @returns {{text: string, tone: Tone} | null}
 */
export function describeExpiry(iso, now = new Date()) {
  const text = describeDate(iso, now);
  if (!text || !iso) return null;
  const left = new Date(iso).getTime() - now.getTime();
  if (left <= 0) return {text: `Expired ${text}`, tone: "stop"};
  if (left <= 2 * DAY) return {text: `Expires ${text}`, tone: "warn"};
  return {text: `Expires ${text}`, tone: "neutral"};
}

/**
 * @param {number} count
 * @param {string} noun
 */
function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}
