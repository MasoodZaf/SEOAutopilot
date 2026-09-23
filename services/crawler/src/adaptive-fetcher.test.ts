import assert from "node:assert/strict";
import test from "node:test";

import {chromium} from "playwright";

import {routeDecision, shouldRender, stubResponse, waitForRenderedContent} from "./adaptive-fetcher.js";
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

/* The rest of this file drives a real browser against a real server, because the
 * defect it guards only exists in one. `waitForRenderedContent` is a claim about
 * *timing* -- that we do not snapshot a page before it has fetched its content --
 * and timing cannot be asserted against a string.
 *
 * The fixture is the shape that broke: a shell that mounts navigation furniture
 * immediately, comfortably past the 200-character threshold, and only then goes
 * and fetches what the page is actually about. Anything keyed on "is there text
 * yet" answers yes before the fetch is even issued.
 *
 * Served from 127.0.0.1, which `assertSafeUrl` forbids -- so this exercises the
 * helper directly rather than the fetcher around it. The policy is not what is
 * under test here.
 */

const CHROME = "Dashboard Challenges Tutorials Pricing About Sign in " +
  "Home / Challenges / Two Sum Difficulty Acceptance Submissions Discuss Editorial " +
  "Previous Next Bookmark Share Report an issue Keyboard shortcuts Settings";

const shellPage = (delayMs: number) => `<!doctype html><html><body>
<div id="chrome">${CHROME}</div><div id="content"></div>
<script>
  fetch("/content?delay=${delayMs}")
    .then(r => r.text())
    .then(t => { document.getElementById("content").textContent = t; });
</script></body></html>`;

const CONTENT = "UNIQUE_CONTENT_MARKER " + "the actual body of the page ".repeat(12);

async function withFixture<T>(delayMs: number, fn: (url: string) => Promise<T>): Promise<T> {
  const {createServer} = await import("node:http");
  const server = createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    if (url.pathname === "/content") {
      const delay = Number(url.searchParams.get("delay") ?? 0);
      setTimeout(() => { res.writeHead(200, {"content-type": "text/plain"}); res.end(CONTENT); }, delay);
      return;
    }
    res.writeHead(200, {"content-type": "text/html"});
    res.end(shellPage(delayMs));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const {port} = server.address() as {port: number};
  try {
    return await fn(`http://127.0.0.1:${port}/`);
  } finally {
    await new Promise<void>(resolve => { server.close(() => resolve()); });
  }
}

test("rendering waits for content the shell has not fetched yet", async () => {
  // The threshold the old wait used is already met by CHROME alone. If that is
  // the readiness signal, this snapshot is taken ~700ms before the content lands.
  assert.ok(CHROME.length > 200, "fixture must clear the old threshold on chrome alone");

  await withFixture(700, async url => {
    const browser = await chromium.launch({headless: true});
    try {
      const page = await browser.newPage();
      await page.goto(url, {waitUntil: "load"});
      await waitForRenderedContent(page);
      const text = await page.evaluate(() => document.body.innerText);
      assert.ok(
        text.includes("UNIQUE_CONTENT_MARKER"),
        `snapshotted the shell without its content: ${JSON.stringify(text.slice(0, 200))}`,
      );
    } finally {
      await browser.close();
    }
  });
});

test("rendering still returns a page whose content never arrives", async () => {
  // A request that never answers must not hang the crawl: the idle wait expires,
  // the paint wait expires, and we keep what the shell did render.
  await withFixture(60_000, async url => {
    const browser = await chromium.launch({headless: true});
    try {
      const page = await browser.newPage();
      await page.goto(url, {waitUntil: "load"});
      const started = Date.now();
      await waitForRenderedContent(page);
      const elapsed = Date.now() - started;
      const text = await page.evaluate(() => document.body.innerText);
      assert.ok(text.includes("Dashboard"), "the shell that did render should be kept");
      assert.ok(!text.includes("UNIQUE_CONTENT_MARKER"));
      assert.ok(elapsed < 25_000, `waited ${elapsed}ms; both waits should be bounded`);
    } finally {
      await browser.close();
    }
  });
});

/* The shape codearc.net actually has: the app mounts, says it is loading, and
 * then goes quiet on the network while it executes before asking for the
 * route's content. Network idle fires inside that quiet gap. */
const pausingShell = (pauseMs: number) => `<!doctype html><html><body>
<div id="chrome">${CHROME}</div><div id="content">Loading challenge... This won't take long!</div>
<script>
  setTimeout(() => fetch("/content?delay=0")
    .then(r => r.text())
    .then(t => { document.getElementById("content").textContent = t; }), ${pauseMs});
</script></body></html>`;

test("rendering does not stop at a loading screen while the app is still working", async () => {
  const {createServer} = await import("node:http");
  const server = createServer((req, res) => {
    if ((req.url ?? "").startsWith("/content")) {
      res.writeHead(200, {"content-type": "text/plain"});
      res.end(CONTENT);
      return;
    }
    res.writeHead(200, {"content-type": "text/html"});
    // Longer than the 500ms network idle needs, so idle fires first.
    res.end(pausingShell(900));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const {port} = server.address() as {port: number};
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${port}/`, {waitUntil: "load"});
    await waitForRenderedContent(page);
    const text = await page.evaluate(() => document.body.innerText);
    assert.ok(text.includes("UNIQUE_CONTENT_MARKER"), `snapshotted the loading screen: ${JSON.stringify(text.slice(-120))}`);
    assert.ok(!text.includes("Loading challenge"));
  } finally {
    await browser.close();
    await new Promise<void>(resolve => { server.close(() => resolve()); });
  }
});

test("off-host sub-resources are answered locally, never fetched and never failed", () => {
  // The SSRF boundary: nothing unsafe continues to the network.
  for (const type of ["script", "stylesheet", "fetch", "xhr", "image", "font", "other"]) {
    assert.notEqual(routeDecision(false, type, false), "continue");
  }
  // A script or stylesheet from another host loads as empty, so the page's own
  // code does not see a failure it may not handle (codearc.net crashed on it).
  assert.equal(routeDecision(false, "script", false), "stub");
  assert.equal(stubResponse("script").contentType, "application/javascript");
  assert.equal(stubResponse("stylesheet").body, "");
  assert.equal(stubResponse("fetch").body, "{}");
  // Navigating away would replace the page under observation, so it is refused.
  assert.equal(routeDecision(false, "document", true), "abort");
  // On the site's own host, only evidence-free heavy types are skipped.
  assert.equal(routeDecision(true, "image", false), "abort");
  assert.equal(routeDecision(true, "script", false), "continue");
  assert.equal(routeDecision(true, "document", true), "continue");
});

test("a ticking clock does not hold a settled page to the deadline", async () => {
  const {createServer} = await import("node:http");
  const server = createServer((_req, res) => {
    res.writeHead(200, {"content-type": "text/html"});
    res.end(`<!doctype html><html><body><div>${CHROME}</div><p>${CONTENT}</p>
      <span id="t">00:00</span>
      <script>let s=0;setInterval(()=>{s++;document.getElementById("t").textContent="00:"+String(s).padStart(2,"0")},300)</script>
      </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const {port} = server.address() as {port: number};
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${port}/`, {waitUntil: "load"});
    const started = Date.now();
    await waitForRenderedContent(page);
    const elapsed = Date.now() - started;
    assert.ok(elapsed < 6_000, `waited ${elapsed}ms on a page whose only change is a clock`);
  } finally {
    await browser.close();
    await new Promise<void>(resolve => { server.close(() => resolve()); });
  }
});
