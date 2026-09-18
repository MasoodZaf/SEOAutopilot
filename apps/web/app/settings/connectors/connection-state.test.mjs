import assert from "node:assert/strict";
import {test} from "node:test";

import {describeDate, describeError, describeExpiry, describeStatus} from "./connection-state.mjs";

const NOW = new Date("2026-09-18T12:00:00Z");

test("a dead grant is loud, and a disconnected one is not", () => {
  assert.deepEqual(describeStatus("reauthorization_required"), {
    label: "Needs reconnecting",
    tone: "stop",
    broken: true,
  });
  assert.equal(describeStatus("revoked").broken, false);
  assert.equal(describeStatus(undefined).label, "Not connected");
});

test("an unknown error code is shown rather than swallowed", () => {
  assert.match(describeError("something_new"), /something_new/);
  assert.equal(describeError(null), null);
  assert.match(describeError("authorization_required"), /connect it again/);
});

test("dates say how far away they are", () => {
  assert.equal(describeDate("2026-09-13T06:00:00Z", NOW), "13 Sept 2026 (5 days ago)");
  assert.equal(describeDate("2026-09-18T20:00:00Z", NOW), "18 Sept 2026 (today)");
  assert.equal(describeDate(null, NOW), null);
});

test("an expiry turns amber two days out and red once passed", () => {
  assert.equal(describeExpiry("2026-09-25T12:00:00Z", NOW)?.tone, "neutral");
  assert.equal(describeExpiry("2026-09-19T12:00:00Z", NOW)?.tone, "warn");
  const lapsed = describeExpiry("2026-09-13T06:00:00Z", NOW);
  assert.equal(lapsed?.tone, "stop");
  assert.match(lapsed?.text ?? "", /^Expired/);
});
