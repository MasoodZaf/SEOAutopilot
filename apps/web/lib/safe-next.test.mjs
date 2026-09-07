import assert from "node:assert/strict";
import test from "node:test";

import {safeNext} from "./safe-next.mjs";

const ORIGIN = "https://seo.oryxenlabs.com";

test("an ordinary path is kept", () => {
  assert.equal(safeNext("/pilot", ORIGIN), "/pilot");
  assert.equal(safeNext("/pilot/workspace?tab=skills", ORIGIN), "/pilot/workspace?tab=skills");
});

test("the parser differential that defeats a string guard", () => {
  // Each of these passes `startsWith("/") && !startsWith("//")` and then
  // resolves to another origin, because the URL parser folds backslashes and
  // strips tab/CR/LF after the string test has run.
  for (const attack of ["/\\evil.com", "/\t/evil.com", "/\r/evil.com", "/\n//evil.com"]) {
    assert.equal(new URL(attack, ORIGIN).origin, "https://evil.com", "precondition");
    assert.equal(safeNext(attack, ORIGIN), "/pilot");
  }
});

test("the obvious hostile forms are refused too", () => {
  for (const attack of ["//evil.com", "https://evil.com/x", "http://evil.com", "javascript:alert(1)"]) {
    assert.equal(safeNext(attack, ORIGIN), "/pilot");
  }
});

test("nothing, or nonsense, falls back", () => {
  assert.equal(safeNext(null, ORIGIN), "/pilot");
  assert.equal(safeNext(undefined, ORIGIN), "/pilot");
  assert.equal(safeNext("", ORIGIN), "/pilot");
});

test("a same-origin absolute URL is reduced to its path", () => {
  assert.equal(safeNext(`${ORIGIN}/settings/members`, ORIGIN), "/settings/members");
  // The fragment is dropped: it never reaches the server anyway.
  assert.equal(safeNext(`${ORIGIN}/pilot#x`, ORIGIN), "/pilot");
});
