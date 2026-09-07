/**
 * Where a sign-in may send somebody afterwards.
 *
 * The obvious guard is a string test — starts with "/", does not start with
 * "//" — and it is wrong, because the value is later handed to `new URL(next,
 * origin)`. The guard matches strings; the sink parses URLs, and the two
 * disagree. For special schemes the WHATWG parser treats a backslash as a
 * slash, and strips tab, CR and LF from anywhere in the input, both *after* a
 * string test has already passed:
 *
 *     "/\\evil.com"    startsWith("/") -> true  -> https://evil.com/
 *     "/\tevil.com"    startsWith("/") -> true  -> https://evil.com/
 *
 * So this resolves first and decides second, against the same parser that will
 * perform the redirect, and returns only the path it agreed on. A parser
 * differential is not a thing to out-guess with a better pattern.
 *
 * @param {string | null | undefined} raw
 * @param {string} origin
 * @returns {string}
 */
export function safeNext(raw, origin) {
  const fallback = "/pilot";
  if (!raw) return fallback;
  let resolved;
  try {
    resolved = new URL(raw, origin);
  } catch {
    return fallback;
  }
  if (resolved.origin !== new URL(origin).origin) return fallback;
  // Rebuild from the parsed parts rather than passing the caller's string on:
  // whatever the parser made of it is what the redirect would have used.
  return `${resolved.pathname}${resolved.search}`;
}
