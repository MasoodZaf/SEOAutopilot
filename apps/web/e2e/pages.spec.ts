import {expect, test} from "@playwright/test";
import {expectAccessible} from "./axe";

for (const [path, heading] of [
  ["/pilot?site=thecalchive.com", "thecalchive.com"],
  ["/pilot/workspace?site=thecalchive.com", "thecalchive.com"],
  ["/settings", /connect/i],
  ["/settings/sites", /sites/i],
  ["/settings/keys", /./],
  ["/settings/members", /members/i],
  ["/settings/billing", /billing/i],
  ["/settings/connectors", /./],
  ["/onboarding", /./],
] as const) {
  test(`signed in ${path} renders and is accessible`, async ({page}) => {
    const response = await page.goto(path);
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading").first()).toBeVisible();
    await expect(page.getByRole("heading", {name: heading}).first()).toBeVisible();
    await expectAccessible(page, path);
  });
}

test("the operations page shows each stage of the change loop", async ({page}) => {
  await page.goto("/pilot?site=thecalchive.com");
  for (const name of ["Governance", "Evidence", "Advisory queue", "Proposals and review", "28-day outcomes"]) {
    await expect(page.getByRole("heading", {name})).toBeVisible();
  }
});
