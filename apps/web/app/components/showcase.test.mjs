import assert from "node:assert/strict";
import test from "node:test";

import {cameFromShowcase} from "./showcase.mjs";

const HOME = "https://oryxenlabs.com/#product-seo-autopilot";

test("a visit from the showcase site counts, with or without www", () => {
  assert.equal(cameFromShowcase("https://oryxenlabs.com/", HOME), true);
  assert.equal(cameFromShowcase("https://www.oryxenlabs.com/products?x=1", HOME), true);
});

test("anywhere else, or nowhere, does not", () => {
  assert.equal(cameFromShowcase("", HOME), false);
  assert.equal(cameFromShowcase("https://google.com/", HOME), false);
  // A lookalike host is not the showcase.
  assert.equal(cameFromShowcase("https://oryxenlabs.com.evil.example/", HOME), false);
  assert.equal(cameFromShowcase("https://seo.oryxenlabs.com/pilot", HOME), false);
});

test("no showcase configured, or a malformed value, means no link", () => {
  assert.equal(cameFromShowcase("https://oryxenlabs.com/", ""), false);
  assert.equal(cameFromShowcase("https://oryxenlabs.com/", undefined), false);
  assert.equal(cameFromShowcase("not a url", HOME), false);
  assert.equal(cameFromShowcase("https://oryxenlabs.com/", "not a url"), false);
});
