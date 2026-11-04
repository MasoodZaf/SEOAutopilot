import assert from "node:assert/strict";
import test from "node:test";

import {decodeCheck, encodeCheck, explainCheck} from "./key-check.mjs";

test("a check survives the redirect and nothing else does", () => {
  const encoded = encodeCheck([
    {model: "gpt-5", status: "model_unavailable"},
    {model: "gpt-4.1", status: "available"},
    {model: "<script>", status: "available"},
  ]);
  assert.equal(encoded, "gpt-5:model_unavailable,gpt-4.1:available");
  assert.deepEqual(decodeCheck(encoded), [
    {model: "gpt-5", status: "model_unavailable"},
    {model: "gpt-4.1", status: "available"},
  ]);
  assert.deepEqual(decodeCheck("gpt-5:owned,<b>:available"), []);
});

test("a key that may not use gpt-5 says so and where to look", () => {
  const advice = explainCheck("openai_api_key", decodeCheck("gpt-5:model_unavailable,gpt-4.1:available"));
  assert.equal(advice.tone, "warn");
  assert.match(advice.text, /won't let it use gpt-5, so drafts fall back to gpt-4\.1/);
  assert.match(advice.text, /Verifications/);
});

test("a working key is good news and a rejected one is not", () => {
  assert.equal(explainCheck("openai_api_key", [{model: "gpt-5", status: "available"}]).tone, "good");
  assert.equal(explainCheck("anthropic_api_key", [{model: "claude-opus-5", status: "key_rejected"}]).tone, "stop");
  assert.equal(explainCheck("perplexity_api_key", [{model: "x", status: "available"}]), null);
});

test("a deployment set to gpt-4.1 says whether the key could use gpt-5", () => {
  const could = explainCheck("openai_api_key", decodeCheck("gpt-4.1:available,gpt-5:available"));
  assert.match(could.text, /drafts are written with gpt-4\.1\. The key could also use gpt-5; this deployment is set to gpt-4\.1\./);
  const cannot = explainCheck("openai_api_key", decodeCheck("gpt-4.1:available,gpt-5:model_unavailable"));
  assert.match(cannot.text, /It cannot use gpt-5 yet\./);
});
