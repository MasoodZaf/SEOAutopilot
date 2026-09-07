import assert from "node:assert/strict";
import {readdirSync, readFileSync, statSync} from "node:fs";
import {dirname, join, relative} from "node:path";
import {test} from "node:test";
import {fileURLToPath} from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));

/**
 * A route handler must not build a redirect from the request it received.
 *
 * Behind the reverse proxy, `request.url` is the address the *proxy* used --
 * the container's hostname and internal port. A redirect built on it sends the
 * browser to a name that resolves nowhere outside the Docker network, and the
 * failure surfaces at the worst possible moment: the first real sign-in
 * completed, minted a session, and then landed on "Safari can't find the
 * server", which reads as authentication being broken rather than working.
 *
 * Neither the type checker nor the build can see this -- every one of those
 * expressions is valid, and the whole flow is correct on localhost, where the
 * request origin and the public origin are the same string. Only a deployment
 * behind a proxy tells them apart, so the check has to be this one: a reader
 * of the source, run on every commit.
 *
 * `lib/app-origin.mjs` is the answer these files should use instead.
 */
const FORBIDDEN = [
  {
    pattern: /new URL\([^)]*,\s*request\.url\s*\)/,
    why: "request.url as a base resolves to the container's internal address",
  },
  {
    pattern: /request\.url\s*\)\s*\.origin/,
    why: "the request's origin is the proxy's view, not the browser's",
  },
  {
    pattern: /\burl\.origin\b/,
    why: "`url` here is parsed from request.url; use appOrigin(request)",
  },
  {
    pattern: /\bnextUrl\.origin\b/,
    why: "nextUrl carries the same internal host as request.url",
  },
];

// The helper itself ends on the request's own origin deliberately: that is the
// correct answer when there is no proxy in front, which is `next dev` and the
// tests. It is the one place allowed to ask.
const EXEMPT = new Set(["lib/app-origin.mjs"]);

function sources(directory) {
  const found = [];
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) {
      found.push(...sources(path));
    } else if (/\.(ts|tsx|mjs)$/.test(entry) && !entry.endsWith(".test.mjs")) {
      found.push(path);
    }
  }
  return found;
}

test("no route handler builds a redirect from the incoming request", () => {
  const files = [...sources(join(WEB, "app")), ...sources(join(WEB, "lib")), join(WEB, "proxy.ts")];
  const offences = [];

  for (const file of files) {
    const name = relative(WEB, file);
    if (EXEMPT.has(name)) continue;
    const lines = readFileSync(file, "utf8").split("\n");
    lines.forEach((line, index) => {
      if (line.trimStart().startsWith("*") || line.trimStart().startsWith("//")) return;
      for (const {pattern, why} of FORBIDDEN) {
        if (pattern.test(line)) offences.push(`${name}:${index + 1} — ${why}\n    ${line.trim()}`);
      }
    });
  }

  assert.deepEqual(offences, [], `\n${offences.join("\n")}\n`);
});

test("the guard reads the files it claims to", () => {
  // A scan that silently matched nothing would pass forever. Assert it found
  // the handlers, so deleting or moving them fails here rather than quietly
  // reducing this to a no-op.
  const files = sources(join(WEB, "app")).map((file) => relative(WEB, file));
  for (const required of [
    "app/auth/callback/route.ts",
    "app/auth/login/route.ts",
    "app/auth/logout/route.ts",
  ]) {
    assert.ok(files.includes(required), `${required} not scanned`);
  }
});
