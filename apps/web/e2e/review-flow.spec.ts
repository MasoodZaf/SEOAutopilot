import {expect, request, test, type APIRequestContext} from "@playwright/test";
import {expectAccessible} from "./axe";

// The change loop a reviewer walks: an opportunity becomes a proposal, the diff
// is read, approval is refused until the reviewer says they read it, then
// recorded; deploying where no repository is connected is refused beside the
// button. Runs on the synthetic load.example site, which has no GitHub
// connector, so nothing here can open a pull request anywhere.
const API = process.env.E2E_API_URL ?? "http://localhost:8001";
const TENANT = process.env.E2E_TENANT_ID ?? "019d0000-0000-7000-8000-00000000c001";
const SITE_HOST = "load.example";

async function api(): Promise<APIRequestContext> {
  const token = process.env.LOCAL_PILOT_AUTH_TOKEN;
  test.skip(!token, "LOCAL_PILOT_AUTH_TOKEN is not set");
  return request.newContext({
    baseURL: API,
    extraHTTPHeaders: {Authorization: `Bearer ${token}`, "X-Tenant-Id": TENANT},
  });
}

async function newProposal(client: APIRequestContext, marker: string) {
  const sites = (await (await client.get("/v1/sites")).json()).data as {id: string; normalized_host: string}[];
  const site = sites.find(s => s.normalized_host === SITE_HOST);
  test.skip(!site, `seed ${SITE_HOST} first`);
  const connectors = await client.get(`/v1/sites/${site!.id}/connectors`);
  if (connectors.ok()) {
    const found = ((await connectors.json()).data ?? []) as {type: string}[];
    expect(found.some(c => c.type === "github"), "load.example must not have a repository").toBe(false);
  }
  const opportunities = (await (await client.get(`/v1/sites/${site!.id}/opportunities?scope=all`)).json()).data as {id: string; page_id: string}[];
  const pick = opportunities[Math.floor(Math.random() * opportunities.length)];
  const created = await client.post(`/v1/sites/${site!.id}/proposals`, {
    headers: {"Idempotency-Key": `e2e-${marker}-${Date.now()}`},
    data: {
      opportunity_id: pick.id,
      page_id: pick.page_id,
      title: `E2E ${marker} ${Date.now()}`,
      rationale: "Shortens the page title to the length search results show.",
      target_type: "github_file",
      target_path: `p/e2e-${marker}.html`,
      before_content: "<title>A very long title that search results will cut off</title>\n",
      after_content: "<title>A shorter title</title>\n",
    },
  });
  expect(created.status(), await created.text()).toBe(201);
  return (await created.json()).data as {id: string; title: string; status: string; risk: string};
}

test("a proposal is read, refused without the self-review tick, then approved", async ({page}) => {
  const client = await api();
  const proposal = await newProposal(client, "approve");
  expect(proposal.risk).toBe("medium");

  await page.goto(`/pilot?site=${SITE_HOST}`);
  const card = page.locator(`#proposal-${proposal.id}`);
  await expect(card.getByRole("heading", {name: proposal.title})).toBeVisible();
  await expect(card.locator("pre")).toContainText("-<title>A very long title");
  await expect(card.locator("pre")).toContainText("+<title>A shorter title</title>");
  await expectAccessible(page, "operations with a proposal");

  // Approving without saying you read it is refused, and the reason is
  // shown on the proposal, not at the top of the page.
  await card.getByRole("button", {name: "Approve"}).click();
  await expect(page.locator(`#proposal-${proposal.id}`).getByRole("alert")).toContainText("I read this diff");

  const again = page.locator(`#proposal-${proposal.id}`);
  await again.getByLabel("I read this diff (only reviewer)").check();
  await again.getByRole("button", {name: "Approve"}).click();
  await expect(page.locator(`#proposal-${proposal.id}`).getByText("approved", {exact: true})).toBeVisible();

  // Deploying needs a connected repository; this site has none.
  await page.locator(`#proposal-${proposal.id}`).getByRole("button", {name: "Deploy"}).click();
  await expect(page.locator(`#proposal-${proposal.id}`).getByRole("alert")).toBeVisible();
  const after = (await (await client.get(`/v1/proposals/${proposal.id}`)).json()).data;
  expect(after.status).toBe("approved");
});

test("a withdrawn proposal is rejected and leaves the review queue", async ({page}) => {
  const client = await api();
  const proposal = await newProposal(client, "withdraw");
  await page.goto(`/pilot?site=${SITE_HOST}`);
  const card = page.locator(`#proposal-${proposal.id}`);
  await card.getByRole("button", {name: "Withdraw"}).click();
  await expect.poll(async () => (await (await client.get(`/v1/proposals/${proposal.id}`)).json()).data.status).toBe("rejected");
  await page.goto(`/pilot?site=${SITE_HOST}`);
  await expect(page.locator(`#proposal-${proposal.id}`).getByRole("button", {name: "Approve"})).toHaveCount(0);
});
