import assert from "node:assert/strict";
import {test} from "node:test";

import {appOrigin} from "./app-origin.mjs";

const CONTAINER = "https://627fe7041447:3000/auth/callback?code=x";

function request(headers = {}) {
  return new Request(CONTAINER, {headers});
}

test("configuration wins over the container's own view of itself", () => {
  process.env.APP_BASE_URL = "https://seo.oryxenlabs.com";
  assert.equal(appOrigin(request()), "https://seo.oryxenlabs.com");
  delete process.env.APP_BASE_URL;
});

test("a trailing path on APP_BASE_URL does not leak into the origin", () => {
  process.env.APP_BASE_URL = "https://seo.oryxenlabs.com/pilot/";
  assert.equal(appOrigin(request()), "https://seo.oryxenlabs.com");
  delete process.env.APP_BASE_URL;
});

test("the forwarding headers are used when nothing is configured", () => {
  assert.equal(
    appOrigin(request({"x-forwarded-host": "seo.oryxenlabs.com", "x-forwarded-proto": "https"})),
    "https://seo.oryxenlabs.com",
  );
});

test("only the first hop of a comma-joined forwarding header is taken", () => {
  assert.equal(
    appOrigin(
      request({
        "x-forwarded-host": "seo.oryxenlabs.com, evil.com",
        "x-forwarded-proto": "https, http",
      }),
    ),
    "https://seo.oryxenlabs.com",
  );
});

test("a forwarded host carrying a path cannot smuggle one into the origin", () => {
  assert.equal(
    appOrigin(request({"x-forwarded-host": "seo.oryxenlabs.com/evil"})),
    "https://seo.oryxenlabs.com",
  );
});

test("a malformed APP_BASE_URL falls through rather than failing the sign-in", () => {
  process.env.APP_BASE_URL = "not a url";
  assert.equal(
    appOrigin(request({"x-forwarded-host": "seo.oryxenlabs.com"})),
    "https://seo.oryxenlabs.com",
  );
  delete process.env.APP_BASE_URL;
});

test("with no proxy and no configuration the request's own origin is right", () => {
  assert.equal(appOrigin(request()), "https://627fe7041447:3000");
});
