import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {dirname, join} from "node:path";
import {test} from "node:test";
import {fileURLToPath} from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));

/**
 * A production web tier with no authentication configured must not serve the
 * control plane.
 *
 * On 2026-09-20 it did. A deploy overwrote `infra/local/web.env` with a stale
 * development copy, the container started with no `OIDC_ISSUER_URL`, and
 * `proxy.ts` opened with `if (!process.env.OIDC_ISSUER_URL) return next()` --
 * so /pilot and /settings/connectors answered 200 to anonymous requests until
 * somebody looked. `required: false` on the env_file meant nothing was logged,
 * and the check that catches it had been failing for unrelated reasons for two
 * days, so a third failure said nothing new.
 *
 * The property is the same one `OPERATOR_IPS` broke in a different shape:
 * absence of configuration must never be read as permission.
 *
 * This is a source assertion rather than a behavioural one because `proxy.ts`
 * imports `next/server`, which `node --test` cannot load without a build step;
 * the sibling tests in this directory read source for the same reason. What it
 * can hold is the one thing that actually went wrong -- that the escape hatch
 * is qualified, and by the environment.
 */
test("the unconfigured escape hatch is confined to development", () => {
  const source = readFileSync(join(WEB, "proxy.ts"), "utf8");

  const hatch = source.match(/if \(!process\.env\.OIDC_ISSUER_URL[^)]*\)/);
  assert.ok(hatch, "proxy.ts no longer tests OIDC_ISSUER_URL; re-read this test");

  assert.match(
    hatch[0],
    /NODE_ENV !== "production"/,
    "an unconfigured issuer returns early without checking the environment, " +
      "which serves /pilot to anonymous requests in production",
  );
});

/**
 * The redirect itself must stay unconditional.
 *
 * The guard is only worth anything if a request with no session cookie ends at
 * sign-in, so this holds the other half: nothing returns `next()` after the
 * cookie test fails.
 */
test("a request with no session cookie is sent to sign in", () => {
  const source = readFileSync(join(WEB, "proxy.ts"), "utf8");
  const body = source.slice(source.indexOf("export function proxy"));

  assert.match(body, /cookies\.has\(SESSION_COOKIE\)/, "the session cookie is no longer checked");
  assert.match(body, /NextResponse\.redirect\(login\)/, "the sign-in redirect is gone");

  // The cookie check must be the last thing that can allow a request through.
  const allowed = [...body.matchAll(/NextResponse\.next\(\)/g)].map((m) => m.index ?? 0);
  const cookieCheck = body.indexOf("cookies.has(SESSION_COOKIE)");
  assert.ok(
    allowed.every((at) => at <= cookieCheck + 80),
    "something lets a request through after the session-cookie check",
  );
});
