import assert from "node:assert/strict";
import {readdirSync, readFileSync, statSync} from "node:fs";
import {dirname, join, relative} from "node:path";
import {test} from "node:test";
import {fileURLToPath} from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const APP = join(WEB, "app");

/**
 * A page that reads tenant data must send a person with no workspace to make
 * one.
 *
 * Every tenant-scoped API route answers `no_tenant_membership` to somebody who
 * has signed in and been invited to nothing. That is not an error -- it is the
 * ordinary first minute of a new account, and it is now the *common* case,
 * because sign-up is self-service: anybody who is given the link arrives here.
 *
 * Each page had absorbed it differently, and all of them wrongly. /pilot caught
 * everything and rendered an empty dashboard offering to add a site, which is a
 * dead end -- there is no workspace to add one to, so the offer fails wherever
 * it is accepted. Settings pages printed the string `no_tenant_membership` at a
 * first-time visitor. The workspace page turned it into a screen of empty
 * panels. Three different wrong answers to one expected state.
 *
 * The type checker cannot see any of this: catching an error and rendering a
 * fallback is valid in every one of those files. Nor can the build, nor a test
 * that mocks the API, because each page is individually self-consistent. What
 * is missing is only visible across the set -- so the check is this one, a
 * reader of the source, run on every commit.
 *
 * `isMissingTenant` in lib/server-api.ts is the shared answer.
 */

/**
 * Pages that read tenant data but must NOT redirect, with the reason.
 *
 * Deliberately a list of exceptions rather than a pattern: a new page is
 * far more likely to have forgotten this than to be a genuine exception, so
 * the default has to be "must handle it", and adding to this list has to be
 * a decision somebody writes a reason for.
 */
const EXEMPT = new Map([
  [
    "app/onboarding/page.tsx",
    "this is the destination -- redirecting to itself is the loop this guards against",
  ],
]);

function pages(dir) {
  const found = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...pages(full));
    } else if (entry === "page.tsx") {
      found.push(full);
    }
  }
  return found;
}

test("a page that reads tenant data routes a workspace-less account to /onboarding", () => {
  const offenders = [];
  let checked = 0;

  for (const file of pages(APP)) {
    const rel = relative(WEB, file);
    const source = readFileSync(file, "utf8");

    // Only pages that actually ask the API for something. A purely static page
    // has no 403 to handle.
    if (!/\bapiJson\b|\bcurrentTenants\b/.test(source)) continue;
    if (EXEMPT.has(rel)) continue;
    checked += 1;

    if (!/\bisMissingTenant\b/.test(source)) {
      offenders.push(`${rel}: reads tenant data without checking isMissingTenant`);
      continue;
    }
    if (!/isMissingTenant\([^)]*\)\)\s*redirect\("\/onboarding"\)/.test(source)) {
      offenders.push(`${rel}: names isMissingTenant but does not redirect to /onboarding`);
    }
  }

  assert.equal(
    offenders.length,
    0,
    `a signed-in person with no workspace would be shown an error or a dead end:\n  ${offenders.join("\n  ")}`,
  );

  // The guard is worthless if the walk silently matched nothing -- a renamed
  // directory would turn this into a test that always passes.
  assert.ok(checked >= 5, `expected to check several tenant-scoped pages, checked ${checked}`);
});

test("the guard reads pages that really exist", () => {
  for (const [rel, why] of EXEMPT) {
    assert.ok(readFileSync(join(WEB, rel), "utf8").length > 0, `${rel} is exempt but missing`);
    assert.ok(why.length > 10, `${rel} is exempt without a reason`);
  }
});
