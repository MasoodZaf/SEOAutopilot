import assert from "node:assert/strict";
import test from "node:test";

import {ProxyPoolManager} from "./proxy-pool.js";

test("ProxyPoolManager rotates healthy proxies and filters by region", () => {
  const pool = new ProxyPoolManager([
    {url: "http://proxy-us-1.example.com:8080", region: "us"},
    {url: "http://proxy-eu-1.example.com:8080", region: "eu"},
  ]);

  assert.equal(pool.getHealthyCount(), 2);

  const proxyUs = pool.getNextProxy("us");
  assert.ok(proxyUs);
  assert.equal(proxyUs.region, "us");

  // Report 3 consecutive failures to mark unhealthy
  pool.reportFailure("http://proxy-us-1.example.com:8080");
  pool.reportFailure("http://proxy-us-1.example.com:8080");
  pool.reportFailure("http://proxy-us-1.example.com:8080");

  assert.equal(pool.getHealthyCount(), 1);
  assert.equal(pool.getNextProxy("us"), null);
});
