import assert from "node:assert/strict";
import test from "node:test";

import {shouldRender} from "./adaptive-fetcher.js";
import type {FetchedResource} from "./types.js";

const resource = (body: string, contentType = "text/html"): FetchedResource => ({
  requestedUrl: "https://example.com/",
  finalUrl: "https://example.com/",
  status: 200,
  contentType,
  body,
  rendered: false,
});

test("auto rendering selects script shells with little visible content", () => {
  assert.equal(shouldRender(resource("<body><div id='app'></div><script src='app.js'></script></body>"), "auto"), true);
});

test("auto rendering ignores large inline script payloads when measuring visible text", () => {
  const body = `<body><main>Loading...</main><script>window.__DATA__=${JSON.stringify("x".repeat(2_000))}</script></body>`;
  assert.equal(shouldRender(resource(body), "auto"), true);
});

test("auto rendering keeps content-rich HTML on the HTTP path", () => {
  assert.equal(shouldRender(resource(`<body>${"useful content ".repeat(30)}</body>`), "auto"), false);
});

test("render policy respects never and ignores non-HTML", () => {
  assert.equal(shouldRender(resource("<script></script>"), "never"), false);
  assert.equal(shouldRender(resource("<script></script>", "application/xml"), "always"), false);
});
