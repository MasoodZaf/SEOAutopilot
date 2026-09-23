import assert from "node:assert/strict";
import {test} from "node:test";

import {lineDiff} from "./line-diff.mjs";

test("unchanged text has no added or removed lines", () => {
  assert.ok(lineDiff("a\nb", "a\nb").every((line) => line.kind === "same"));
});

test("an edited line shows as removed then added, the rest unchanged", () => {
  const diff = lineDiff("intro\nold claim\noutro", "intro\nverified claim\noutro");
  assert.deepEqual(
    diff.map((line) => `${line.kind}:${line.text}`),
    ["same:intro", "removed:old claim", "added:verified claim", "same:outro"],
  );
});

test("appended lines are additions", () => {
  assert.deepEqual(
    lineDiff("a", "a\nb").map((line) => line.kind),
    ["same", "added"],
  );
});

test("CRLF and LF versions of the same text do not differ", () => {
  assert.ok(lineDiff("a\nb\nc", "a\r\nb\r\nc").every((line) => line.kind === "same"));
});
