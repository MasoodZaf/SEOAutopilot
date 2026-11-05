import {expect, test} from "@playwright/test";
import {expectAccessible} from "./axe";

for (const [path, heading] of [
  ["/", /./],
  ["/login", /sign in/i],
  ["/privacy", /privacy policy/i],
  ["/terms", /terms/i],
  ["/tutorial", /./],
] as const) {
  test(`public ${path} renders, fits the screen and is accessible`, async ({page}) => {
    const response = await page.goto(path);
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading", {level: 1}).first()).toHaveText(heading);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, "page scrolls sideways").toBeLessThanOrEqual(0);
    await expectAccessible(page, path);
  });
}

test("the policy says where the data is", async ({page}) => {
  await page.goto("/privacy");
  await expect(page.getByText("Helsinki, Finland")).toBeVisible();
  await expect(page.getByText(/AI provider whose key you added/).first()).toBeVisible();
});
