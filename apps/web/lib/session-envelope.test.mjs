import assert from "node:assert/strict";
import {randomBytes} from "node:crypto";
import test from "node:test";

import {open, sameState, seal} from "./session-envelope.mjs";

const key = randomBytes(32);

test("a sealed session comes back exactly as it went in", () => {
  const session = {accessToken: "id-token", expiresAt: 1770000000, email: "ada@example.com"};
  assert.deepEqual(open(key, seal(key, session)), session);
});

test("the token is not readable from the cookie value", () => {
  const envelope = seal(key, {accessToken: "must-not-be-readable"});
  assert.ok(!envelope.includes("must-not-be-readable"));
  assert.ok(!Buffer.from(envelope.split(".")[2], "base64url").toString("utf8").includes("must-not"));
});

test("a tampered cookie is no session at all, not a broken one", () => {
  const envelope = seal(key, {accessToken: "id-token"});
  const [iv, tag, body] = envelope.split(".");
  const flipped = Buffer.from(body, "base64url");
  flipped[0] ^= 0x01;
  assert.equal(open(key, [iv, tag, flipped.toString("base64url")].join(".")), null);
});

test("a cookie sealed with another key does not open", () => {
  assert.equal(open(randomBytes(32), seal(key, {accessToken: "id-token"})), null);
});

test("nonsense in the cookie is refused rather than thrown", () => {
  for (const value of [undefined, "", "not-an-envelope", "a.b", "a.b.c.d", "!!.??.**"]) {
    assert.equal(open(key, value), null);
  }
});

test("state comparison rejects a mismatch and tolerates unequal lengths", () => {
  assert.equal(sameState("abc", "abc"), true);
  assert.equal(sameState("abc", "abd"), false);
  assert.equal(sameState("abc", "abcd"), false);
});
