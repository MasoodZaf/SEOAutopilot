import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readdirSync, readFileSync, statSync} from "node:fs";
import {dirname, join, relative} from "node:path";
import {test} from "node:test";
import {fileURLToPath} from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const APP = join(WEB, "app");
const REGISTRY = join(APP, "components", "tips.tsx");

/**
 * A tooltip has to reach a screen reader, not only a mouse.
 *
 * The balloons are CSS pseudo-elements. Pseudo-elements are decoration: they
 * are not in the accessibility tree. So the first version of this feature
 * showed forty explanations to sighted pointer users and none to anybody else
 * -- and the explanations are exactly the reassurances a cautious operator
 * wants before pressing something on a dashboard that can edit a live website
 * ("dry run, changes nothing", "a human still merges it").
 *
 * The fix is `aria-describedby` on each control pointing at a description in
 * `components/tips.tsx`. Nothing in the type checker or the build can see
 * whether those two agree: a `data-tip` with no matching description is valid
 * TSX that renders a balloon and announces nothing, and a dangling
 * `aria-describedby` is valid HTML that silently describes nothing. So the
 * check has to be this one, a reader of the source.
 */

const id = (text) => "tip-" + createHash("sha1").update(text, "utf8").digest("hex").slice(0, 10);

function sources(dir) {
  const found = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) found.push(...sources(full));
    else if (entry.endsWith(".tsx") && full !== REGISTRY) found.push(full);
  }
  return found;
}

function collect() {
  const used = new Map(); // text -> [files]
  let tipCount = 0;
  let describedCount = 0;
  for (const file of sources(APP)) {
    const src = readFileSync(file, "utf8");
    for (const m of src.matchAll(/data-tip="([^"]*)"/g)) {
      tipCount += 1;
      const list = used.get(m[1]) ?? [];
      list.push(relative(WEB, file));
      used.set(m[1], list);
    }
    describedCount += [...src.matchAll(/aria-describedby="tip-[0-9a-f]{10}"/g)].length;
  }
  return {used, tipCount, describedCount};
}

function declared() {
  const src = readFileSync(REGISTRY, "utf8");
  const out = new Map(); // id -> text
  for (const m of src.matchAll(/<span id="(tip-[0-9a-f]{10})">\{("(?:[^"\\]|\\.)*")\}<\/span>/g)) {
    out.set(m[1], JSON.parse(m[2]));
  }
  return out;
}

test("every tooltip has a description a screen reader can reach", () => {
  const {used} = collect();
  const have = declared();

  const missing = [];
  for (const [text, files] of used) {
    const wanted = id(text);
    if (!have.has(wanted)) {
      missing.push(`${files[0]}: "${text.slice(0, 48)}…" has no description in components/tips.tsx`);
    } else if (have.get(wanted) !== text) {
      missing.push(`${files[0]}: description for "${text.slice(0, 32)}…" has drifted from the tooltip`);
    }
  }
  assert.equal(missing.length, 0, `tooltips that announce nothing:\n  ${missing.join("\n  ")}`);
});

test("no description is declared that nothing points at", () => {
  const {used} = collect();
  const wanted = new Set([...used.keys()].map(id));
  const orphans = [...declared().keys()].filter((key) => !wanted.has(key));
  assert.equal(
    orphans.length,
    0,
    `components/tips.tsx declares descriptions no control references: ${orphans.join(", ")}`,
  );
});

test("every tipped control is wired to its description", () => {
  const {tipCount, describedCount} = collect();
  // One aria-describedby per data-tip. A tooltip added without wiring shows a
  // balloon and announces nothing, which is the exact defect this guards.
  assert.equal(
    describedCount,
    tipCount,
    `${tipCount} controls carry data-tip but ${describedCount} carry aria-describedby — ` +
      "a tooltip was added without wiring it to a description",
  );
});

test("the guard is actually looking at something", () => {
  const {tipCount} = collect();
  assert.ok(tipCount >= 20, `expected the app to carry many tooltips, found ${tipCount}`);
  assert.ok(declared().size >= 20, "the description registry looks empty");
});
