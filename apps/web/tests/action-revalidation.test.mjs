import assert from "node:assert/strict";
import {readdirSync, readFileSync, statSync} from "node:fs";
import {dirname, join, relative} from "node:path";
import {test} from "node:test";
import {fileURLToPath} from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));

/**
 * A server action must not redirect without invalidating the page it returns to.
 *
 * Every action in this app mutates through the API and then redirects back to
 * the page the button was on. Next.js serves that redirect from the client
 * router cache, and when the target is the URL already on screen it is a no-op
 * navigation: the mutation reaches the API and the page keeps rendering exactly
 * what it rendered before.
 *
 * On 2026-09-08 that made the pilot dashboard behave as though every button but
 * the first were dead. Withdraw a proposal and the next click did nothing, with
 * no error and no change, until the page was reloaded by hand -- nineteen
 * withdrawals, nineteen manual reloads. The worse half is that a real refusal
 * looked identical: a deployment refused with 409 by the daily change budget
 * was indistinguishable from a click that never registered, and was only found
 * by reading the API's access log.
 *
 * Neither the type checker nor the build can see it. `redirect(...)` is correct
 * TypeScript and the flow is right on a first click, which is what anybody
 * testing by hand does. It only shows up on the second action against the same
 * URL, so the check has to be this one: a reader of the source, on every commit.
 *
 * The fix each file carries is a local `redirectFresh` that revalidates first.
 * It is the only place allowed to call `redirect` directly.
 */
const HELPER = "redirectFresh";

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

/** Files declaring "use server" at the top: every one of them is an action module. */
function actionModules() {
  return sources(join(WEB, "app")).filter((file) => {
    const head = readFileSync(file, "utf8").slice(0, 200);
    return /^\s*["']use server["'];/.test(head);
  });
}

function statements(file) {
  return readFileSync(file, "utf8")
    .split("\n")
    .map((line, index) => ({line, number: index + 1}))
    .filter(({line}) => {
      const start = line.trimStart();
      return !start.startsWith("*") && !start.startsWith("//") && !start.startsWith("/*");
    });
}

test("a server action never redirects without revalidating", () => {
  const offences = [];

  for (const file of actionModules()) {
    const name = relative(WEB, file);
    const body = readFileSync(file, "utf8");

    if (!body.includes("revalidatePath")) {
      offences.push(`${name} — redirects without ever calling revalidatePath`);
      continue;
    }

    // Exactly one bare `redirect(` is allowed: the one inside the helper,
    // immediately after the revalidation. Anything else is an action taking the
    // cached path back to a page it just changed.
    let seenHelper = false;
    for (const {line, number} of statements(file)) {
      if (line.includes(`function ${HELPER}(`)) seenHelper = true;
      const bare = /(?<![A-Za-z0-9_])redirect\(/.test(line);
      if (!bare) continue;
      const insideHelper = seenHelper && line.trim() === "redirect(path);";
      if (!insideHelper) {
        offences.push(`${name}:${number} — redirect() outside ${HELPER}\n    ${line.trim()}`);
      }
    }
  }

  assert.deepEqual(offences, [], `\n${offences.join("\n")}\n`);
});

test("the guard reads the action modules it claims to", () => {
  // A scan that matched nothing would pass forever. Name the modules whose
  // buttons this defect actually broke, so moving or deleting one fails here
  // rather than quietly turning the guard into a no-op.
  const found = actionModules().map((file) => relative(WEB, file));
  for (const required of [
    "app/pilot/actions.ts",
    "app/settings/connectors/actions.ts",
    "app/settings/members/actions.ts",
  ]) {
    assert.ok(found.includes(required), `${required} not scanned`);
  }
});
