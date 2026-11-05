import {defineConfig, devices} from "@playwright/test";

// Runs against a stack that is already up: the API on :8001 with migrations
// applied and e2e/fixture.sql loaded (psql ... -f e2e/fixture.sql),
// and `next dev` (the only build that accepts the local pilot token).
//   docker compose up -d postgres redis api worker crawler
//   set -a; . ../../infra/local/web.env; set +a
//   API_BASE_URL=http://localhost:8001 npx next dev -p 3917 -H 127.0.0.1
//   E2E_BASE_URL=http://127.0.0.1:3917 pnpm e2e
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: {timeout: 30_000},
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3917",
    trace: "retain-on-failure",
  },
  projects: [
    {name: "desktop", use: {...devices["Desktop Chrome"]}},
    {name: "phone", use: {...devices["Pixel 7"]}, testMatch: /public|pages/},
  ],
});
