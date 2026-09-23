/**
 * A line diff, for showing a reviewer what changed from the AI's draft.
 *
 * Longest-common-subsequence over lines. Posts are a few hundred lines at
 * most, so the quadratic table is small; past the cap it degrades to "all
 * removed, all added" rather than spending seconds on a render.
 */
/** @typedef {{kind: "same" | "added" | "removed", text: string}} DiffLine */

const CAP = 1500;

/**
 * @param {string} before
 * @param {string} after
 * @returns {DiffLine[]}
 */
export function lineDiff(before, after) {
  const a = before.split(/\r?\n/);
  const b = after.split(/\r?\n/);
  if (a.length > CAP || b.length > CAP) {
    return [
      ...a.map((text) => ({kind: /** @type {const} */ ("removed"), text})),
      ...b.map((text) => ({kind: /** @type {const} */ ("added"), text})),
    ];
  }
  const rows = a.length + 1;
  const cols = b.length + 1;
  const table = new Uint32Array(rows * cols);
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i * cols + j] =
        a[i] === b[j]
          ? table[(i + 1) * cols + j + 1] + 1
          : Math.max(table[(i + 1) * cols + j], table[i * cols + j + 1]);
    }
  }
  /** @type {DiffLine[]} */
  const out = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({kind: "same", text: a[i]});
      i += 1;
      j += 1;
    } else if (table[(i + 1) * cols + j] >= table[i * cols + j + 1]) {
      out.push({kind: "removed", text: a[i]});
      i += 1;
    } else {
      out.push({kind: "added", text: b[j]});
      j += 1;
    }
  }
  while (i < a.length) out.push({kind: "removed", text: a[i++]});
  while (j < b.length) out.push({kind: "added", text: b[j++]});
  return out;
}
