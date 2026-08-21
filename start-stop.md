# SEO Autopilot — Start/Stop and Continuity Ledger

**Repository:** `/Users/masoodzafar/ITHustle/SEOAutopilot`  
**Last updated:** 2026-08-20 (Asia/Karachi)  
**Current stage:** Local controlled pilot onboarding for `codearc.net`  
**Current mode:** Observe  
**Deployment status:** Disabled  
**Autopilot status:** Disabled

This file is the restart point for future development sessions. It records what was requested, what
was implemented, what was proven, what remains unproven, and the next safe action. Do not interpret
local test success as production readiness, provider approval, ranking accuracy, or launch approval.

## 1. Product decision and original request

The product is an enterprise-ready, multi-tenant SEO Autopilot. Its purpose is to connect crawl,
Google Search Console, analytics, performance, repository/CMS, and bounded AI evidence; rank the
highest-value SEO opportunities; prepare reviewable changes; enforce approval; deploy through a
controlled connector; and measure results.

Target stack:

- Next.js frontend.
- FastAPI backend.
- PostgreSQL source of truth.
- Redis coordination and work streams.
- Playwright/HTTP crawler.
- Google Search Console API.
- GA4 integration.
- Lighthouse/PageSpeed integration.
- GitHub and CMS connectors.
- Provider-neutral LLM abstraction with OpenAI available locally.

Product modes:

- **Observe:** evidence, findings, and scores only; no proposals or deployment effects.
- **Recommend:** reviewable proposals; explicit approval required.
- **Autopilot:** only policy-allowlisted, low-risk automatic changes; disabled by default.

Core agents:

1. Technical SEO Agent.
2. Content SEO Agent.
3. Keyword Opportunity Agent.
4. Internal Linking Agent.
5. SEO Content Agent.
6. SEO Performance Agent.
7. GEO/AI Search Visibility Agent.

MVP order:

1. Connect and verify a site.
2. Crawl it safely.
3. Connect Google Search Console.
4. Score every eligible page.
5. Find the top 20 opportunities.
6. Propose bounded fixes.
7. Require review and approval.
8. Deploy through a GitHub PR or CMS staging/publish connector.
9. Track the result against a frozen baseline.

The user named SerpApi's `awesome-seo-tools` repository as a competitor and required the product to
be better. Superiority must be demonstrated through measurable workflow depth—evidence ingestion,
deterministic prioritization, explainability, safe delivery, rollback, auditability, and outcome
measurement—not through feature-count claims.

`codearc.net` was selected as the first controlled real-site pilot. Its results will be used to
evaluate product accuracy and usefulness.

## 2. Repository foundation and project artifacts

The production-oriented monorepo foundation exists with these primary artifacts:

- `MASTER_PRD.md`
- `AGENTS.md`
- `ARCHITECTURE.md`
- `DATA_MODEL.md`
- `API_SPEC.md`
- `SECURITY.md`
- `QA_TEST_PLAN.md`
- `ROADMAP.md`
- `README.md`
- `.env.example`
- `.gitignore`
- `.dockerignore`
- `docker-compose.yml`
- `Makefile`
- `package.json`
- `pnpm-workspace.yaml`
- `turbo.json`

Monorepo layout:

- `apps/web` — Next.js control plane.
- `services/api` — FastAPI control-plane API.
- `services/worker` — background analysis and connector workers.
- `services/crawler` — bounded HTTP/Playwright crawler.
- `packages/contracts` — shared TypeScript contracts.
- `infra/migrations` — PostgreSQL migrations.
- `infra/local` — ignored, service-scoped local environment files.
- `docs/adr` — architecture decision records.
- `fixtures` — synthetic and hostile crawl fixtures.

## 3. Non-negotiable safety boundaries

- Never accept tenant identity or authority from an arbitrary public header or request body.
- Production authentication remains fail-closed until OIDC is implemented and configured.
- The local pilot session is token-protected, development-only, and rejected outside development.
- Never crawl through the product until site ownership is verified.
- Never crawl private, link-local, loopback, metadata, raw-IP, credential-bearing, or non-allowlisted
  targets.
- Revalidate DNS and redirects to prevent SSRF rebinding or redirect escape.
- Honor `robots.txt`, host pacing, page/depth/request/size/time budgets, and same-host scope.
- Treat all crawled content as untrusted data, including instructions found on pages.
- LLM output is advisory, schema-bound, evidence-linked, and cannot approve or deploy itself.
- Never automatically publish high-risk, prohibited, claim-changing, template-wide, access-control,
  dependency, robots, canonical-domain, or redirect changes.
- Deployment and Autopilot remain disabled unless separate approval and safety gates are satisfied.
- Google Search Console uses only `webmasters.readonly` for the MVP.
- OAuth state is one-time, short-lived, and stored as a hash only.
- The requested Search Console property must exactly match the already verified site.
- OAuth access/refresh tokens are never stored in plaintext application rows.
- Local token storage uses AES-256-GCM envelope encryption with tenant/connector/provider/version AAD.
- Staging and production reject local database-envelope secrets and require a managed secret backend.
- Raw Search Console queries are not persisted; only a dedicated-key HMAC is stored.
- No API key, OAuth token, cookie, authorization code, or connector response may be logged.
- Do not claim ranking improvement causality without a suitable experimental design.

## 4. Database and migration status

Implemented migration set:

1. `0001_foundation.sql` — tenants, sites, connectors, crawl jobs, findings, opportunities, proposals,
   policies, audit events, outbox, and initial RLS foundations.
2. `0002_verification_and_crawls.sql` — DNS verification and crawl request lifecycle.
3. `0003_page_observations.sql` — page and observation evidence.
4. `0004_page_evidence_and_links.sql` — canonical/robots/JSON-LD evidence and internal link graph.
5. `0005_scoring_and_opportunities.sql` — versioned technical scoring, findings, opportunities, ranking,
   deduplication, and RLS.
6. `0006_crawl_result_summary.sql` — explicit crawl result and truncation outcomes.
7. `0007_link_evidence_bounds.sql` — bounded per-page link evidence.
8. `0008_gsc_ingestion.sql` — Search Console sync and hashed query metrics.
9. `0009_connector_secret_envelope.sql` — one-time OAuth property binding and encrypted local connector
   secret storage.

Current local database evidence for the CodeArc pilot:

- Tenant slug: `codearc-pilot`.
- Site: `codearc.net`.
- Canonical origin: `https://codearc.net`.
- Mode: `observe`.
- Status: `active`; DNS ownership verification succeeded through the real application workflow.
- The verification challenge was consumed and the verification token was stored only as a hash.
- The CodeArc Search Console connector is active for the exact URL-prefix property
  `https://codearc.net/` with the read-only scope only.
- The connector row contains an opaque database-envelope secret reference; encrypted credential
  material remains tenant/connector bound and is not present in this ledger.

## 5. Crawler implementation status

Implemented and tested:

- HTTP-first crawling.
- Playwright rendering only when the rendering policy requires it.
- Same-host URL normalization and containment.
- Manual redirect validation.
- Private/link-local/metadata IP denial.
- `robots.txt` retrieval and enforcement.
- Sitemap discovery and nested sitemap parsing.
- Query-trap rejection.
- Page-count and traversal-depth bounds.
- Discovery budget bounded relative to requested page count.
- HTTP/rendered response size limits.
- Browser-request limits.
- Per-page internal-link persistence bounds.
- Explicit truncation evidence.
- Redis stream handling for new and reclaimed pending jobs.
- Terminal persistence before job acknowledgement.
- Synthetic cross-tenant evidence tests.
- A 500-page hostile fixture test.

Default live pilot crawl request:

```json
{
  "kind": "full",
  "max_pages": 500,
  "max_depth": 10,
  "render_policy": "auto"
}
```

The first live CodeArc crawl completed with terminal status `partial` because it reached the explicit
500-page limit. It persisted 500 page observations with zero fetch errors, zero robots skips, and no
discovery truncation. This is bounded pilot evidence, not full coverage of the 945-URL sitemap.

## 6. Evidence, scoring, and opportunity status

Implemented:

- Immutable crawl observations.
- Page inventory and stable opaque cursor pagination.
- Page evidence including status, final URL, canonical, title, meta description, H1s, word count,
  content hash, robots directives, rendering path, JSON-LD, and bounded links.
- Deterministic technical rules and page score output.
- Versioned `technical-v1` scoring records.
- Deduplicated findings and opportunities.
- Deterministic top-opportunity ordering with repeated-rule diversity, then score, fingerprint, and ID.
- Suppressed/prohibited opportunity exclusion.
- Tenant-isolated page, finding, score, and opportunity reads.

Important limitation:

- `technical-v1` is provisional engineering logic, not an approved or calibrated SEO accuracy model.
- CodeArc produced 500 page scores, 1,495 findings, and 1,495 page-level opportunities.
- The first usefulness check found that score-only ordering made all top 20 entries the same missing-H1
  rule. The API ranking was corrected to deterministic rule rounds. The current top 20 contains 7
  missing-H1, 7 meta-description-range, and 6 thin-content opportunities.
- Accuracy and usefulness cannot be claimed until evidence is manually reviewed against the live site
  and Search Console data.

## 7. Google Cloud and Search Console configuration

Google Cloud project created:

- Project name: `SEO Autopilot`.
- Project ID: `seo-autopilot-506008`.
- Organization: `mas-zaf-org`.
- Billing account was attached with user approval.

Google configuration completed:

- Search Console API `searchconsole.googleapis.com` is enabled.
- Google Auth Platform app name: `SEO Autopilot`.
- Audience: External.
- Publishing status: Testing.
- Support/contact account: `mas.zaf@gmail.com`.
- Test user: `mas.zaf@gmail.com`.
- Granted application data scope: `https://www.googleapis.com/auth/webmasters.readonly` only.
- OAuth client name: `SEO Autopilot Local`.
- OAuth client type: Web application.
- Exact callback: `http://localhost:8000/v1/connectors/oauth/callback`.
- The user accepted the Google API Services User Data Policy during configuration.

Local credential handling:

- Google client ID and secret are stored only in ignored `.env.local`.
- `.env.local` has local-user-only permissions.
- The downloaded plaintext Google credential JSON was moved to Trash after import.
- OAuth values were never printed into chat or shell output. During the first callback, the default
  Uvicorn access logger recorded the consumed one-time authorization code in the old container log.
  A query-redaction filter was added and live-tested, and the old API container/log was discarded.
  Access/refresh tokens and encrypted secret payloads were not logged.
- Connector envelope and Search Console query-hash keys were generated locally.
- `.env.local` is ignored by `.gitignore` through `.env.*`.
- `infra/local/web.env` is independently ignored and contains only the scoped local pilot token.

Verified account/property evidence:

- The signed-in Google account can access the URL-prefix property `https://codearc.net/`.
- Search Console was visibly showing that property.
- Real OAuth consent completed through SEO Autopilot. The one-time state was consumed, the connector
  is active, and the stored property and scope exactly match the verified CodeArc site and
  `webmasters.readonly`.
- A read-only 28-day Search Console backfill for 2026-07-21 through 2026-08-17 completed. It
  checkpointed all 28 days and persisted 15 hashed-query metric rows covering 10 dates, with 0 clicks
  and 16 impressions in the returned data. Sparse data must not be overinterpreted.

If Google presents a passkey, password, OTP, CAPTCHA, or account-selection prompt, the user must take
over. Never ask the user to share a passkey, password, or OTP.

## 8. Actual web application pilot

The web app is not only a static landing page anymore. A real local pilot workflow was added:

- Landing-page CTA links to `/pilot`.
- Local pilot session bootstrap through the API.
- Development-only, exact bearer-token authentication.
- Fixed local pilot tenant and actor identities.
- CodeArc site creation through the real API and application service.
- Observe-mode status display.
- DNS challenge creation through the real API.
- DNS record display and verification action.
- Exact Search Console authorization action for `https://codearc.net/`.
- Connector callback/status page.
- First bounded crawl action.
- Live authenticated top-opportunity summary grouped for human review, with score, confidence, risk,
  and explicit Observe-mode/non-causality language.
- Errors shown beside the workflow.
- No production effects or automatic changes.

Local authentication safety:

- Enabled only when `APP_ENV=development`.
- Requires `LOCAL_PILOT_AUTH_ENABLED=true` and a secret of at least 32 characters.
- Rejected by configuration validation in staging/production.
- The browser never receives the API bearer token.
- Next.js server actions call the API server-side.
- The web service receives only its scoped pilot token, not OpenAI or Google connector credentials.

Container security improvements made during the live pilot:

- The crawler no longer receives `.env.local` and therefore cannot access OpenAI or Google secrets.
- PostgreSQL and Redis are internal-only and are not published to host ports.
- API is bound to `127.0.0.1:8000`.
- Web is bound to `127.0.0.1:3001`.
- Port 3000 belongs to an unrelated `seek-frontend` project and was deliberately left untouched.
- The web production image copies the pnpm content-addressed runtime store needed by Next standalone.

Current application URLs:

- Web landing: `http://localhost:3001/`
- CodeArc pilot: `http://localhost:3001/pilot`
- Connector status: `http://localhost:3001/settings/connectors`
- API/OpenAPI: `http://localhost:8000/docs`
- API readiness: `http://localhost:8000/v1/system/readiness`

## 9. Current running service state

At the time of this ledger update, these Docker Compose services were running:

- `seo-autopilot-web-1` — bound to `127.0.0.1:3001`.
- `seo-autopilot-api-1` — bound to `127.0.0.1:8000`.
- `seo-autopilot-worker-1` — internal network only.
- `seo-autopilot-crawler-1` — internal network only, read-only filesystem, reduced capabilities.
- `seo-autopilot-postgres-1` — healthy, internal network only.
- `seo-autopilot-redis-1` — healthy, internal network only.

The readiness endpoint returned the safe foundation state:

- `deployments_enabled=false`
- `autopilot_enabled=false`
- production authentication is not configured

The local pilot authentication is deliberately separate from production OIDC readiness.

## 10. CodeArc public-site reconnaissance

The user explicitly authorized public access to `codearc.net`. Read-only reconnaissance was performed,
but it was not persisted as a product crawl and did not bypass site verification.

Homepage observations:

- The page initially rendered a `Loading...` shell before client-side content appeared.
- Final title: `Learn to Code for GCSE, A-Level & IB Computer Science | CodeArc`.
- Canonical: `https://codearc.net/`.
- Robots meta: `index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1`.
- One H1 was present after rendering.
- Seven JSON-LD blocks were present after rendering.
- Twenty-six same-host links were present in the rendered homepage snapshot.
- The homepage emitted browser console warnings labelled `Performance Budget Violation`.
- These are preliminary observations only; the verified crawler and evidence rules must reproduce them
  before they become product findings.

Public `robots.txt` observations:

- General crawling is allowed.
- `/api/`, `/dashboard`, `/settings`, `/profile`, and `/auth/` are disallowed.
- Sitemap declared at `https://codearc.net/sitemap.xml`.
- AhrefsBot and MJ12bot receive a crawl delay of 10 seconds.

Public sitemap observations:

- `https://codearc.net/sitemap.xml` returned HTTP 200.
- It contained 945 URL entries at inspection time.
- No query-string URL was found in the focused check.
- No sitemap URL matched the focused disallowed-path check.
- A 500-page pilot limit therefore cannot represent full sitemap coverage; the crawl must report
  truncation/partial coverage clearly.

Do not present these reconnaissance values as a complete SEO audit or verified product accuracy.

## 11. Verification and quality evidence

Latest full `make check` result:

- TypeScript lint: passed.
- TypeScript type checks: passed.
- Python Ruff: passed for API and worker.
- Python Pyright: 0 errors and 0 warnings for API and worker.
- API tests: **54 passed**.
- Worker tests: **15 passed**.
- Crawler tests: **17 passed**.
- Contract tests: **1 passed**.
- Next.js production build: passed.
- Dynamic routes built: `/pilot`, `/pilot/review/[itemId]`, and `/settings/connectors`.
- The deployed `/pilot` page was visually verified in the in-app browser with CodeArc active,
  Search Console connected, and the 7/7/6 top-opportunity distribution rendered.

Known test warning:

- FastAPI/Starlette emitted one deprecation warning about its current TestClient/httpx integration.
- The web package currently has no automated browser/unit test cases; browser verification was manual.

Test-isolation fixes made:

- Settings tests now explicitly disable `.env.local` loading.
- Connector service tests now explicitly disable `.env.local` loading.
- This prevents real local credentials and pilot flags from changing unit-test behavior.

## 12. Calibration workflow and corrective evidence run

The first auditable calibration workflow is now implemented:

- Migration `0010_calibration_reviews.sql` adds tenant-scoped calibration runs, frozen items, and
  append-only reviewer labels with RLS, composite tenant foreign keys, request hashes, and
  idempotency keys.
- The API can create/read the current calibration run, read an item, and append a review. Authorized
  reviewer roles are enforced server-side and relevant changes emit audit/outbox events.
- Accuracy, actionability, severity fit, progress, and per-rule precision are available in the local
  pilot UI. Uncertain labels are excluded from precision rather than counted as correct.
- Calibration is restricted to the latest terminal crawl. A run returns
  `409 calibration_evidence_not_ready` if the newest crawl is active or if a selected opportunity
  references older crawl evidence.
- The workflow remains an Observe-mode evidence activity. It cannot propose, approve, publish, or
  enable Autopilot.

An initial 20-item set was created and then cancelled without deletion after browser comparison found
an evidence defect. Its source HTTP observation reported missing H1 and only 26 words for a tutorial
URL, while the rendered public navigation reached CodeArc's sign-in page with an H1. This is evidence
of a specific extraction/render mismatch, not a sitewide accuracy conclusion, and no human label was
submitted on the user's behalf.

Corrective work completed:

- Adaptive-render selection now measures visible HTML after excluding script, style, noscript,
  template, and SVG payloads.
- The stream consumer acknowledges permanently unclaimable events so an old poison event cannot
  starve current crawl work. Temporarily unclaimable active jobs remain pending for recovery, and
  valid jobs still remain unacknowledged until terminal persistence.
- Active crawls now refresh both the database lease and Redis pending-event ownership every 30
  seconds. This prevents another worker from reclaiming a long traversal and allows an expired job to
  be recovered after a crawler restart.
- The crawler image pins its pnpm bootstrap through npm to avoid the observed Corepack download
  timeout.
- Corrective CodeArc crawl `7d210635-7207-4671-b971-3aa0e0158207` ran with a 500-page,
  depth-10, automatic-render boundary. A crawler rebuild exposed an orphaned running-job recovery
  defect before any observations were persisted. The exact expired, zero-observation job was
  requeued with an audit/outbox recovery event and reclaimed as attempt 2. It reached terminal
  `partial` with 500 observations, all 500 rendered, zero fetch errors, and 500 distinct final URLs.
  Analysis completed with 500 scores, 1,495 findings, and 1,495 opportunities.
- Migration `0011_one_active_crawl.sql` now enforces one queued/running crawl per tenant/site. The API
  exposes the tenant-scoped latest crawl and returns `409 crawl_already_active` before duplicate
  enqueue; the partial unique index closes concurrent-request races.
- The local pilot now shows the authoritative latest crawl status. While it is queued/running, the UI
  removes both the duplicate crawl action and the stale-evidence calibration action. Browser proof
  showed `running`, “A second crawl cannot start,” and “Wait for the latest crawl and analysis.”
- The aggregate Search Console endpoint/UI is implemented with a default 28-day window, 90-day cap,
  tenant/site scope, derived CTR, impression-weighted position, and no raw query/hash output. The live
  CodeArc default window showed 0 clicks, 11 impressions, 0.0% CTR, and average position 45.9; the UI
  explicitly labels this sparse evidence and forbids conclusions from it.
- Fresh calibration run `47387fa7-9c97-4965-8082-074c779e3157` is open with 20 items: 7 missing-H1,
  7 description-length, and 6 thin-content. All 20 frozen items reference corrective crawl
  `7d210635-7207-4671-b971-3aa0e0158207`. No reviewer label has been entered.

## 13. Exact current stop point

The protected activation loop is proven locally for CodeArc:

- DNS verification: completed.
- Search Console OAuth: active, exact property, read-only scope.
- First crawl: terminal `partial`, 500 observations persisted.
- Technical analysis: 500 scores, 1,495 findings, 1,495 opportunities.
- Search Console backfill: terminal `completed`, 28 days checkpointed, 15 rows persisted.
- Top-20 API: authenticated, tenant-scoped, and diversified across the three current rule classes.
- Mode: Observe.
- Proposals and deployments: not started.
- Autopilot: disabled.

The next product gate is human accuracy/usefulness review of the newly frozen corrective top-20
set—not automatic change generation.

## 14. Next safe steps

1. Human-review the 20 frozen items in the live app, then calculate precision/false-positive rates by
   finding class and record actionability/severity-fit ratings.
2. Add GA4 and PageSpeed/Lighthouse evidence before introducing performance-weighted prioritization.
3. Do not enable Recommend mode until the user reviews the findings and accepts the calibration.
4. Do not enable deployment or Autopilot during this pilot without a new explicit approval and the
    applicable product/security gates.

## 15. Accuracy and usefulness evaluation plan

The CodeArc pilot should answer these questions with evidence:

- **Coverage:** How many eligible sitemap/internal-link pages were discovered, fetched, rendered, and
  persisted? What was excluded or truncated?
- **Technical accuracy:** For a manually reviewed sample, what percentage of findings are true
  positives? Track results by rule type, not only an aggregate score.
- **Severity accuracy:** Do severity and confidence match human assessment?
- **Prioritization quality:** How many of the top 20 are genuinely worth action, and how many important
  items were ranked lower or missed?
- **Evidence quality:** Can every finding be reproduced from stored observations and timestamps?
- **Search opportunity quality:** Do GSC-derived opportunities use sufficient impressions/clicks and
  avoid overinterpreting sparse data?
- **Usefulness:** Would an SEO lead accept, edit, dismiss, or defer each opportunity? Record the reason.
- **Safety:** Were robots, tenant boundaries, site scope, permissions, and change restrictions obeyed?
- **Performance:** Time to first crawl, time to first useful opportunity, job reliability, and cost.
- **Outcome loop:** Only after an approved change is deployed later, compare frozen baseline and
  follow-up windows with explicit caveats; report association, not guaranteed causality.

Suggested pilot report metrics:

- Crawl coverage and explicit truncation percentage.
- Finding precision by class.
- Top-20 acceptance/actionability rate.
- False-positive and duplicate rate.
- Evidence completeness rate.
- Median confidence calibration gap.
- Number of safety-policy violations: target zero.
- Number of unauthorized or automatic effects: target zero.

## 16. Known gaps and release blockers

- The first crawl is capped at 500 of 945 sitemap URLs, so site coverage is incomplete.
- Search Console data is sparse (16 impressions and no clicks in the completed 28-day backfill); it is
  ingestion proof, not sufficient evidence for search-opportunity conclusions.
- The original 1,495 technical findings and their severity/priority have not been human-calibrated;
  their first calibration set was cancelled after an extraction/render mismatch was demonstrated.
- Top-20 diversity is currently derived from opportunity type/title because the API model does not yet
  expose a stable grouped rule key or sitewide issue aggregate.
- The score model is not calibrated or product-approved.
- Production OIDC, membership, SSO, and SCIM are not implemented.
- Refresh-token rotation, revocation, provider quota evidence, and managed production secret storage
  remain connector release gates.
- GA4, GitHub deployment, CMS connectors, proposal approval, rollback, and measurement workflows
  remain roadmap work. PageSpeed/Lighthouse now has an asynchronous bounded mobile lab path and one
  successful CodeArc observation, but trend coverage, quota/cost evidence, and field data remain open.
- The web workflow has manual browser proof but no automated web test suite yet.
- The repository currently shows all project files as untracked; no initial commit was created.
- No GitHub push or pull request was authorized or performed.
- Local proof does not satisfy provider approval, security review, design-partner approval, or launch
  approval.

## 17. Start and stop commands

Run from:

```text
/Users/masoodzafar/ITHustle/SEOAutopilot
```

Start the local application:

```bash
docker compose up -d postgres redis api web worker crawler
```

Check status:

```bash
docker compose ps
```

Check the API readiness endpoint:

```bash
curl http://localhost:8000/v1/system/readiness
```

Run the complete quality gate:

```bash
make check
```

Stop the application without deleting volumes:

```bash
docker compose stop
```

Do not run `docker compose down -v` unless the user explicitly approves deleting the local PostgreSQL
and Redis data volumes.

## 18. Secret-handling restart checklist

Before every continuation:

1. Confirm `.env.local` is ignored with `git check-ignore -v .env.local`.
2. Confirm `infra/local/web.env` is ignored.
3. Never print either file.
4. Never show secret values through shell output, browser snapshots, logs, screenshots, or chat.
5. Confirm the crawler has no `.env.local` injection.
6. Confirm deployment and Autopilot flags remain false.
7. If a credential is suspected to be exposed, stop, revoke/rotate it, and record the incident.

## 19. Final continuity statement

Resume at review item 1 in calibration run `47387fa7-9c97-4965-8082-074c779e3157`; do not invent or
submit the human labels. Google Cloud project `seo-autopilot-506008` now has the PageSpeed Insights
API enabled and an API-only restricted key named `SEO Autopilot PageSpeed Local`. Its value exists
only as `PAGESPEED_API_KEY` in ignored, mode-0600 `.env.local`; no value was recorded in chat, logs,
source, or documentation. Keyed run `09dc4976-5da1-498a-80dd-af9c50996437` froze
`https://codearc.net/` from corrective crawl `7d210635-7207-4671-b971-3aa0e0158207` and completed on
attempt 1 with Lighthouse 13.4.1: performance 42/100, LCP 11,058 ms, CLS 0.017078, TTFB 2 ms, and no
lab INP. This single lab observation is diagnostic evidence, not field Core Web Vitals, a trend, or
ranking-impact proof. Preserve Observe mode and all verification, OAuth, tenant, crawler, evidence,
provider-quota, and secret boundaries.
