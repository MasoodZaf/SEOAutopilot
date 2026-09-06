# Roadmap

## Phase 0 — Product and risk closure (2 weeks)

**Deliverables:** approved PRD, ADRs, threat model, scoring rubric v0, UX flows, design-partner profile, first CMS decision, provider scope review, synthetic fixture spec.

**Exit:** G0 approved; no unresolved decision that would change tenancy, identity, connector authority, or deployment semantics.

## Phase 1 — Platform foundation (3 weeks)

- Monorepo, CI, environments, identity/tenant context, RBAC skeleton.
- PostgreSQL migrations/RLS, Redis job/outbox pattern, audit events.
- Site CRUD, verification challenge, connector credential boundary.
- Provider-neutral DNS ownership framework: universal manual TXT verification plus Owner/Admin
  two-consent adapters, exact zone binding, scoped-credential storage, and one server-derived TXT
  record only. Cloudflare is the first adapter; Route 53, GoDaddy, Namecheap, and other adapters can
  be added without changing the site-verification lifecycle. Local/test uses the encrypted envelope;
  managed-secret certification remains required before staging/production.
- Observability, feature flags, usage budgets, kill-switch controls.

**Exit:** isolation and authorization test matrix passes; restore and migration rehearsal documented.

## Phase 2 — Crawl and evidence graph (4 weeks)

- HTTP-first crawler with Playwright fallback, robots/rate/scope policy, SSRF controls.
- Sitemap/page inventory, canonical normalization, link graph, content/performance observations.
- GSC OAuth and resumable sync; optional GA4/PageSpeed enrichments behind flags.
- Page inventory and crawl UI.
- Frozen top-20 human calibration set with per-rule precision and actionability evidence.

**Exit:** deterministic 500-page hostile fixture completes within budget; no scope escapes; evidence lineage is complete.

Current implementation checkpoint (2026-08-20): the HTTP-first crawler, SSRF controls, robots and
sitemap discovery, bounded adaptive Playwright fallback, canonical/meta/heading/JSON-LD evidence,
internal link-edge persistence, tenant RLS, signed cursor page inventory, durable versioned technical
scores/findings, deterministic top-opportunity APIs, and the local deterministic 500-page hostile
scope-containment fixture are implemented. The GSC foundation now includes read-only authorization
intent, tenant-bound short-lived OAuth state, connector/sync/metric RLS, daily 25,000-row pagination,
durable resume contracts, idempotent metric keys, and query HMAC privacy with mocked provider tests.
The local/test OAuth callback now consumes locked state once, enforces exact read-only scope and
verified property access, and rotates AES-256-GCM database envelopes without exposing token fields.
The live pilot now reports the latest tenant-scoped crawl, enforces one active crawl per site at API
and database layers, and blocks stale-evidence calibration while that crawl is active. The frozen
top-20 calibration workflow is implemented, but CodeArc precision remains unmeasured until the
corrective rendered crawl finishes and a human labels the new evidence set.
The tenant-scoped aggregate GSC summary is also live with bounded dates and no query/hash exposure.
PageSpeed now has a tenant-scoped, idempotent, asynchronous mobile lab-run path, immutable bounded
metrics, a leased worker consumer, and a pilot read model. Google Cloud now has the API enabled and
an API-only restricted local key; the first keyed CodeArc run completed and stored a Lighthouse
summary. Quota/cost evidence, repeated trend coverage, production secret management, and field data
remain open. A managed production secret adapter,
refresh/revocation worker flow, durable connector event consumer, and production-network capacity
evidence also remain open; therefore the overall Phase 2 exit gate has not passed.

## Phase 3 — Scoring and top 20 (3 weeks)

- Deterministic rules, versioned scoring, page health factors.
- Technical, keyword opportunity, internal linking, content, performance, and GEO advisory workflows.
- LLM adapter with schema/cost/redaction controls and agent eval harness.
- Opportunity workbench with evidence and suppression.

**Exit:** seeded benchmark issues meet ranking thresholds; identical input/version reproduces top 20; injection and hallucinated-evidence evals pass.

## Phase 4 — Recommend workflow (4 weeks)

- Proposal revisions, bounded diffs, validators, policy engine, approvals and expiry.
- GitHub App connector: branch/commit/PR, webhook status, drift detection.
- First CMS staging connector if Phase 0 approves scope.
- Audit export and notification hooks.

**Exit:** synthetic end-to-end Recommend workflow passes with idempotency, separation of duties, drift, and failure cases.

## Phase 5 — Measurement and design-partner alpha (3 weeks)

- Post-deploy verification, baseline/follow-up windows, annotations, outcome dashboards.
- Connector health, tenant cost allocation, support and incident runbooks.
- Controlled design-partner onboarding in Observe/Recommend only.

**Exit:** G3 evidence complete; customer authorization and DPA recorded; no P0/P1 defects; support owner accepts runbook.

## Phase 6 — Autopilot beta (4+ weeks)

- Low-risk allowlist only, daily/site budgets, freeze windows, canary batches.
- Global/tenant kill switches, automatic halt and tested rollback.
- Policy simulation against historical proposals before activation.

**Exit:** G5 approval after reliability window; zero prohibited auto-deploys; rollback drills and independent security review findings closed.

## Post-MVP

- Additional CMSs and enterprise Git providers via certification suite.
- SAML/SCIM, regional residency, dedicated tenant deployments.
- Controlled experiments and stronger causal measurement.
- Competitor/SERP data providers where terms and economics are approved.
- Public connector/agent SDK, signed packages, marketplace governance.
- AI visibility monitoring based on independently observable, reproducible evidence.

## Competitive milestones

Against the SerpApi Awesome SEO Tools catalog, breadth is tracked as coverage but does not drive launch. Quarterly benchmark dimensions are: connected evidence sources, issue classes detected, end-to-end tasks completed without tool switching, explainability completeness, safe deployment coverage, rollback reliability, and measured-outcome coverage. “Better” requires benchmark evidence on these dimensions, not a marketing claim or a larger catalog.

## Safety correction checkpoint (2026-08-28)

- Deployment now fails closed unless the global flag, actor role, site mode, freeze state/window, and
  daily budget all permit it. Only a development/test mock adapter is reachable.
- Placeholder GitHub/CMS adapters, live verification, and rollback no longer fabricate successful
  provider effects; their certification work remains in Phases 4-6.
- Measurement now requires independent verification and the complete follow-up window, and product
  copy labels it as association rather than causal inference.
- Production RLS effectiveness remains blocked on a least-privileged non-owner database runtime role
  and live cross-tenant denial evidence.

## Three-site design-partner checkpoint (2026-08-28)

- The standalone control plane now has an explicit allowlist for `codearc.net`, `thecalchive.com`,
  and `wordkitapp.com`; arbitrary client-supplied hosts fail closed to the primary pilot.
- CodeArc remains active in Observe mode. TheCalcHive and WordKit are locally onboarded as
  `pending_verification`; no crawl or connector action is allowed until site-specific DNS proof passes.
- The live workbench shows a deterministic top-20 advisory queue with affected URL, suggested
  correction, required validation, score, confidence, and risk. It exposes zero automatic-change
  controls and requires human calibration before Recommend mode.
- Private pilot release still requires three-site crawl/calibration evidence. Public Recommend
  release additionally requires production identity/membership, effective RLS, certified GitHub or
  CMS staging plus rollback, browser E2E/accessibility evidence, operations runbooks, and G3 approval.

## Always-on workflow checkpoint (2026-09-02)

Feature parity work against hosted always-on SEO agents began with the scheduling substrate, since
every other recurring capability depends on it.

- Delivered: `routine`/`routine_run` scheduling with deterministic daily, weekly, and monthly slots;
  idempotent claiming across worker replicas; collapsed catch-up after downtime; `site_audit` and
  `weekly_report` execution; deterministic `report` payloads hashed for reproducibility; AES-256-GCM
  notification channels with https-only, public-address-checked delivery.
- Fails closed: a routine requires an active DNS-verified site, skips while a site is frozen, skips
  when a crawl is already active, and parks after five consecutive failures. A frozen site publishes
  no work at all.
- Recorded honestly: `keyword_refresh`, `sitemap_coverage`, `competitor_scan`, and
  `ai_visibility_scan` are accepted as routine kinds but record `skipped` with
  `routine_kind_not_implemented` until their phases land. They do not report success.
- Keyword workspace: GSC query terms are now sealed per site in an AES-256-GCM envelope alongside
  the existing HMAC, which unlocked deterministic token-overlap clustering, rule-based intent,
  question-share AEO candidacy, striking-distance and cannibalisation signals, and a bounded
  opportunity score. Raw terms are readable only through one role-gated, audited endpoint.
- Sitemap coverage: the crawler now records which sitemaps it considered, how it found each one,
  and every in-scope URL declared, so declared-versus-crawled-versus-indexable is measured rather
  than inferred.
- Content briefs and the refresh queue: each cluster above the demand threshold produces a
  deterministic brief naming every gap it found in the target page's stored evidence, with a
  priority that rises with gap count. Briefs are advisory artifacts with no deployment authority.
- Keyword clustering now stems tokens before comparing them, so "percentage calculator", "how to
  calculate percentage", and "what is a percentage calculator" form one cluster. Labels are built
  from the original words, so a stem never reaches the reader.
- Competitor tracking: competitors and their pages are entered by a human and fetched with no
  discovery, under a shared egress policy, the competitor's robots.txt, and manual redirect
  revalidation. Only structure is stored, never body text.
- Answer-engine readiness: measured from first-party crawl and search evidence, with weights
  renormalised over the factors actually measured. It never claims to observe citations, and the
  snapshot column admits no citation source but `none`.
- Still open: observed answer-engine citations and SERP rank tracking still need a certified
  provider decision on terms and economics. Nothing in the product reports them today.

## Agent workspace checkpoint (2026-09-02)

- The control plane now has a Chat / Tasks / Skills / Reports workspace over twelve skills.
- Routing is deterministic scored token overlap against a code-defined registry, not a model call.
  A message cannot name a capability that is not in the registry, a role cannot reach a skill it is
  not allowed, and an unclear request starts nothing and offers choices instead.
- Read skills answer from stored records and return the references used. No skill can deploy,
  approve, or write site content.
- Scheduling skills cover all seven routine kinds, so any recurring workflow can be set up from
  chat. They require a word asking for the work to happen, not merely the topic, and honour a named
  cadence ("every day", "every Monday", "every month"). Without a cadence they queue one run and
  leave the routine parked.
- An LLM classifier remains a possible future addition behind the existing adapter's schema, cost,
  and evidence-validation controls. It is not required and is not wired in: the deterministic
  router is the shipped behaviour.

## Delivery principles

- Ship vertical slices: evidence -> opportunity -> proposal -> deploy -> measure.
- Feature flags default closed for external writes.
- Provider feasibility, account creation, successful local mocks, and prepared documents are not launch approval.
- Autopilot scope expands only after policy simulation and production evidence; never by default.

## QA/QC checkpoint (2026-09-06)

An adversarial review of the shipped paths, run against the live local stack rather than the test
suite alone, found five defects. All five are fixed and verified; the gate is green at 189 API tests,
92 worker tests, ruff and pyright clean on both services, and web lint/typecheck/test/build passing.

- **Two-approver rule defeated by one approver.** `approve_proposal` counted prior approvals after
  `session.add()`. SQLAlchemy autoflushes the pending row into that query, and the `+ 1` then counted
  the same person twice, so the first approver alone satisfied `required_approver_count = 2` on every
  medium and high risk proposal. Fixed by counting distinct approver ids before staging the new row.
  The missing-key default also moved from 1 to 2 so a damaged policy record fails closed.
- **Indexing-control changes classified as low risk.** Risk was read from `target_path` only, so a
  canonical rewritten to another domain, a `noindex` meta robots tag, or a refresh redirect took one
  approver, admitted the editor role, and was auto-deployable — contradicting the `AGENTS.md` rule
  that canonical, robots and redirect changes never auto-deploy. `detect_control_directive_changes`
  now forces high risk on any change to those directives, and reports which one in the policy record.
- **Server actions swallowed their own redirects.** `redirect()` throws; fifteen catch blocks in
  `apps/web/app/pilot/actions.ts` rewrote that signal into a generic `unexpected-error`, discarding
  precise codes such as `verification-challenge-expired`. Guarded with `unstable_rethrow`.
- **Policy logic forked.** `services/worker/app/proposals/` held a byte-identical copy of the API's
  policy and validator that only its own tests imported. Removed; its tests were ported onto the live
  implementation.
- **Prompt-injection guards lived in the mock provider.** Moved into `GuardedLLMProvider`, which runs
  the injection, budget and evidence-citation checks around every provider call. A provider that
  implements no checks of its own is now tested to still be refused.

The root cause of the first defect is structural, not incidental: every service test drives an
`AsyncMock` session, so no test can observe how a real session behaves. `test_unit_of_work.py`
already documented this blind spot and guarded one instance of it. Hardening track H1 below removes
the cause rather than the next symptom.

## Product decisions (2026-09-06)

- **Operating mode is the tenant's choice, not a product-wide stance.** Observe, Recommend and
  Autopilot are per-site settings a customer selects, with a mode ceiling the platform enforces.
  This makes Autopilot in-scope rather than deferred, and it gives `can_auto_deploy` a real consumer
  for the first time — it is currently computed and read by nothing. Autopilot therefore needs the
  blast-radius design in Phase 6 before any tenant can switch it on, and the low-risk allowlist must
  exclude the indexing-control directives now classified as high risk.
- **CodeArc is the primary pilot.** It has low traffic *because* its SEO is poor, which makes it the
  cleaner measurement subject: a near-zero baseline gives an unambiguous before/after signal, where
  an already-ranking site would need a synthetic control to separate the change from its own trend.
  The trade is a slower measurement cycle and a hard dependency on opening the login gate to the
  crawler. TheCalcHive and WordKit remain comparison sites.

## Path to production readiness

The original Phase 2 exit gate has not passed, and Phases 4 through 6 have never executed against a
real connector. These tracks sequence the remaining work. H1 and H2 have no external dependencies and
run first; H6 is a business constraint, not a build one.

### H1 — Restore tenant isolation, and the harness that proves it

The harness landed first and immediately found that **row-level security has never been in effect**.
All 50 tenant tables enable it and every policy is written correctly, but two independent conditions
make them inert, and both must be fixed:

1. No table sets `FORCE ROW LEVEL SECURITY`, and the API connects as the role that ran the
   migrations, so it owns every table. An owner bypasses its own policies unless they are forced.
2. That role is a `SUPERUSER` with `BYPASSRLS` — the postgres image makes `POSTGRES_USER` a
   superuser. A superuser ignores row security unconditionally and `FORCE` does not apply to it.
   Forcing alone was measured on a clean schema and changed nothing.

Measured on a database built only from `infra/migrations`, so this is the schema's behaviour and not
an artefact of the dev volume: with `app.tenant_id` set to a tenant owning no rows, the application
role still saw every site. Isolation today rests entirely on the explicit `tenant_id` filters in the
service layer; any query that omits one — and some rely on RLS instead of filtering — is unisolated.

The remedy is verified end to end: a `NOSUPERUSER NOBYPASSRLS` application role granted only DML,
with `FORCE` on for defence in depth. Under it a tenant sees only its own rows, an unknown scope and
an unset scope both see nothing, and a cross-tenant insert is rejected by the policy's `WITH CHECK`.

**Done (2026-09-06):**

- Migration `0027` forces row-level security on all 50 tables, driven from the catalogue rather than
  a hand-written list because the defect being fixed is precisely that a table can be missed. It also
  creates `seo_autopilot_app` (NOSUPERUSER, NOBYPASSRLS, DML only) and `seo_autopilot_relay`
  (BYPASSRLS, for the genuinely cross-tenant sweeps). Both are created `NOLOGIN` with no password;
  granting a secret is an operational step so no credential enters the repository.
- The API connects as `seo_autopilot_app`. Verified live: the probe that saw all 5 sites under a
  tenant scope owning nothing now sees 0, and both read and write paths are unchanged.
- The OAuth callback keeps its one unavoidable unscoped read behind a narrow SELECT-only policy on
  `connector_oauth_state` gated by `app.oauth_callback`, then adopts the resolved row's tenant scope
  before the locking re-read that consumes the state. Five integration tests hold that exception to
  its shape: one table, read only, no other table opened, and state unspendable outside a scope.
- The isolation tests now pass with their xfail markers removed.

- The worker now holds two identities. `app/tenancy.py` provides a `tenant_scope` context manager
  that the pagespeed and routine consumers wrap each unit of work in; the analysis and GSC consumers
  already scoped themselves. The outbox dispatcher, routine scheduler and notification dispatcher
  claim work across tenants before any scope exists, so they run on `seo_autopilot_relay` from a
  separate pool. Scope is session level, not transaction level, because a unit of work spans an
  outbound HTTP call and a transaction held across a network call would keep row locks for its
  duration; asyncpg resets session state on release, and the explicit clear does not depend on that.
- Verified live: `pg_stat_activity` shows the services on `seo_autopilot_app` and
  `seo_autopilot_relay` only, a weekly report routine and a PageSpeed run both complete end to end
  under the scoped role, and the only superuser connection is an operator's psql.

**Remaining:**

- Provision both role passwords in deployment and document the step in `DEPLOYMENT.md`. Production
  is still entirely unpatched: applying 0027 alone changes nothing, because forcing row security
  does not constrain a superuser.
- Move the remaining safety controls off `AsyncMock`. **Done for deployment, connector sync and
  performance** — `tests/integration/test_deployment_gate.py` and `test_idempotency_races.py` cover
  the mode ceiling, emergency freeze, scheduled freeze window, daily change budget, idempotency under
  concurrency, role check and global switch against real PostgreSQL on the `seo_autopilot_app` role.
  The value was measured, not assumed: removing the tenant and day scoping from the budget count — so
  one tenant's deployments consume another's, and the budget counts all of history — leaves all 31
  mocked proposal tests green, and fails the new ones. Writing them also turned up a live defect on
  the deployment path, since fixed.
- ~~Calibration has no real coverage at all.~~ **Done.** `tests/integration/test_calibration_run.py`
  seeds three pages, each with its own observation, finding and opportunity, and covers evidence
  readiness, the open-run check, idempotency including the concurrent case, review recording, and
  tenant isolation of the frozen set. Its idempotency guard is now verified rather than asserted:
  reverting it fails the concurrency case.
- ~~Cursor scoping.~~ **Done.** `tests/integration/test_page_cursor.py` walks a real inventory whose
  pages all share a `last_seen_at`, as a crawl leaves them, and proves no page is lost or repeated;
  dropping the `page_id` tiebreak leaves 7 of 25 pages reachable while the three existing cursor
  tests stay green. It also shows a signed cursor being accepted by the token and refused by the
  service for another tenant, since the token binds to a site and not to who holds it.

**H1 is closed.** Deployed 2026-09-06. Production runs the API and crawler as `seo_autopilot_app`
and the worker as that plus `seo_autopilot_relay`; `pg_stat_activity` shows no service on
`seo_autopilot`. Measured on the host as the application role: unscoped `SELECT count(*) FROM site`
returns 0, a bogus tenant 0, the pilot tenant 3, and the role cannot bypass row security. Before the
deploy the same query returned every row. Migration `0028` ran there too, suppressing 3765 findings
and opportunities across three collapsed crawls and deleting 1424 fictitious page scores. A crawl of
thecalchive.com afterwards produced 30 pages with 30 distinct content hashes and a 0.033 top share,
exercising API, outbox relay, crawler and analysis under the scoped roles.

**Exit:** no service connects as a superuser in production, and every control named in `AGENTS.md`
has a test that runs against real PostgreSQL.

### Orphaned worker runs (found and closed 2026-09-06)

Exercising the PageSpeed path surfaced a defect unrelated to isolation. The client never mapped
`httpx` transport failures onto `PageSpeedError`, so a read timeout escaped the consumer's handler,
which only catches `PageSpeedError` and `ValueError` to mark a run failed and release its lease. The
run was left `running`, and because `performance_run_already_active` rejects a new run while one is
active, a single network timeout wedged that site's PageSpeed runs permanently. The mapping is fixed
and covered by tests.

The deeper gap is now closed too. The claim query does reclaim an expired lease, but only when a
message is redelivered, and the observed run had no pending Redis entry despite the failing branch
never acking — so database lease state and stream pending state could diverge and leave a run with
no route back. `services/worker/app/reaper.py` is the missing half: a cross-tenant sweep on the relay
identity that, for a row whose lease expired five minutes past its deadline, either resets it to
`queued` and republishes the delivery event in one statement, or — when its attempts are spent —
records `status='failed'` with `error_code='lease_expired'` so a dead run is visible rather than
pending. It covers all four stream-driven tables (`crawl_job`, `performance_run`, `routine_run`,
`connector_sync`), which answers the "same shape on the other consumers" question.

Two judgement calls worth knowing. `notification_delivery` is excluded because its dispatcher already
re-reads the table every tick, so sweeping it would race two writers for no gain. And `connector_sync`
tracks no attempt count, so there is no safe bound on requeueing it — an abandoned sync is failed
instead, with its cursor preserved so a fresh sync resumes rather than restarts. Giving that table an
`attempts` column would let it be retried like the others.

Verified against the running stack: a run stranded at `attempts=1` was requeued and republished, the
dispatcher relayed the event 0.19s later, and the PageSpeed consumer re-claimed it; the same run
stranded at `attempts=3` was marked failed with `lease_expired`.

### H2 — Make the evidence real

**The CodeArc evidence set is invalid, and the low yield on the other sites was never the bug.**
Measured 2026-09-06:

| site | observations | distinct content hashes | findings |
|---|---|---|---|
| codearc.net | 2500 | **5** | 1534 |
| thecalchive.com | 96 | 32 (one per page) | 1 |
| wordkitapp.com | 26 | 13 (one per page) | 1 |

Every CodeArc page returns HTTP 200 with the same 30-word body, the same site-wide title, and no H1.
The crawler is recording an app shell or login wall, not the tutorials. Because it is a 200, nothing
marked the crawl failed. The two sites the crawler can actually see return unique content per page
and yield one finding each — that is the honest signal, and those sites are close to clean.

So all 1534 CodeArc findings are artifacts: `h1.missing` on 513 of 513 pages, `content.thin` on 513
of 513, `description.length` on 508. Everything derived from them is equally invalid — the page
scores, the 100+ opportunities, the frozen top-20 calibration set, the weekly digest, and the advice
the chat agent gives, which currently reports missing H1s on tutorial pages that in reality have
them. The system is confidently describing problems that do not exist on the primary pilot site.

**Work:**

- ~~Add an evidence-integrity guard before analysis.~~ **Done.** `completeCrawl` measures the
  observations it is about to finish with; a crawl where one body covers 80% of successful fetches,
  or where distinct bodies fall to a tenth of the page count, is marked `failed` with
  `error_code='content_collapse'` and emits no `crawl.completed` event, so analysis never sees it.
  Terminal rather than requeued — refetching the same wall produces the same wall. Small crawls are
  exempt, since a three-page site sharing a body is a plausible site. Verified against the live site:
  a fresh 60-page CodeArc crawl returned 10 distinct bodies at a 0.85 top share and was refused,
  where the old code would have produced roughly 180 more fictitious findings.
- ~~Quarantine the existing CodeArc findings, opportunities and calibration set.~~ **Done** in
  migration `0028`, which derives the affected crawls from the stored hashes with the same thresholds
  rather than naming a site. On the dev database it suppressed 1534 findings and 1534 opportunities,
  cancelled the open calibration run, and deleted 2500 page scores, leaving the 124 real ones and the
  two honest findings untouched. Statuses are reversible and observations are kept.
- Still open: give CodeArc a way to be crawled (crawler-UA allowlist, signed bypass token, or
  server-side rendering for the crawler) and re-crawl. Until then the site has no valid evidence, and
  the guard now says so out loud instead of inventing some.
- ~~Run the crawl scenarios against the in-repo hostile fixture, including a shell-serving fixture
  that exercises the guard end to end.~~ **Done.** `createShellWallFixture` serves one body under
  HTTP 200 on every URL, discovered through a sitemap. Crawled through the real engine it yields 120
  observations, zero fetch errors, every status 200, one content hash, one title and no h1 anywhere —
  so everything a crawl normally reports says it went well. The 500-page hostile fixture is asserted
  *not* to trip the guard, since a guard that failed real crawls would be worse than the gap it
  closes.

**Exit:** a completed CodeArc crawl whose distinct content hashes track its page count, and a
findings count that survives the integrity guard.

**Note on the pilot choice.** Until CodeArc is crawlable, it cannot serve as the measurement subject
— its baseline measures a login wall, not the site. TheCalcHive is the usable before/after signal in
the meantime.

### H3 — Close the change loop

- Real GitHub adapter: branch, commit, PR, drift detection against `base_hash`, idempotency key
  yielding exactly one PR. The manifest and PR-body formatter already exist.
- Post-deploy verification by re-crawling the target URL and diffing against expectation.
- Rollback with incident and audit records.

**Preparing the first real deployment (2026-09-06).** TheCalcHive is the subject; its source is the
public `MasoodZaf/mindTools` repository, so the change is a commit in the customer's own repository
rather than an overlay. Walking the site's 32 findings to a proposal turned up three things.

- **The rule was right about the pages and wrong about the reason.** `content.title_h1_mismatch`
  fired 32 times because it compared raw tokens: "Free Online Calculators" in the title did not match
  "Every calculator you'll ever need" in the heading, the same subject in two grammatical numbers.
  It now compares through `similarity_tokens`, the stemmer the keyword clusterer already uses, and
  drops from 32 pages to the 18 whose titles genuinely share no subject with the heading.
- **The defect it was pointing at is site-level.** One hero H1 is copied onto 32 pages, so it
  identifies none of them; on a calculator page it is also `display:none`, leaving the page with no
  visible H1 at all. No per-page rule can see that, so `analyze_crawl` now counts H1 text across the
  crawl and `h1.duplicate_across_site` reports the count in its own summary.
- **The false positive was on the front page, and it was the dangerous one.** There the shared H1 is
  the hero headline: correct, deliberate, and the only page where it is visible. A proposal derived
  from the title would have replaced a hand-written headline with the brand string on the site's most
  valuable page. `plan_repair` refuses the site root outright.

`app/domain/h1_repair.py` derives the replacement from the page's own `<title>`, so the first real
change needs no language model and is byte-verifiable: the document must be identical either side of
the one span, and re-reading the result must return the intended heading. It refuses a page with no
H1, with several, with an unusable title, or already correct. Nothing yet turns an opportunity into a
proposal automatically; that is the remaining gap in this track.

**Mock deployments no longer key off `app_env`.** The route admitted the mock adapter whenever
`app_env` was development or test, and production runs `app_env=development` until H5 lands, so
enabling deployments there would have made an adapter that fabricates success reachable on the live
host. It now requires `MOCK_DEPLOYMENTS_ENABLED`, which the settings validator refuses outside
development and test. Restoring the old condition fails `test_deploy_route_gate.py`.

**Exit:** QA_TEST_PLAN scenarios 11, 12 and 13 pass against a real test repository, not the mock
adapter.

### H4 — Real generation behind the guardrail

- Implement a provider against `GuardedLLMProvider`. Scope generation to drafting — meta
  descriptions, brief prose, H1 suggestions. Findings, scoring and policy stay deterministic.
- Wire `wrap_untrusted_evidence` into prompt construction; it is currently unreferenced.
- Build the eval set: labeled expected findings, evidence precision and recall, unsupported-claim
  rate, cost and latency.

**Exit:** QA_TEST_PLAN scenario 7 becomes an end-to-end gate rather than a unit proof.

### H5 — Multi-user identity

- OIDC, sessions, real accounts and role assignment; retire the local-pilot bearer token.
- Separation of duties is currently only provable in the negative — one actor can be refused, but two
  distinct humans approving in sequence cannot be exercised at all.

**Exit:** two real accounts complete an author-then-approve cycle on a medium-risk proposal.

### H6 — Measurable outcome

All three pilot sites currently report zero search impressions, so verification and measurement can
be built correctly and still prove nothing. CodeArc's own traffic recovery is the first measurement
subject; a design partner with existing traffic remains the faster path to a second one.

**Exit:** a deployed change with a baseline and follow-up window, reported without presenting
association as causation.

### Remaining quality gates

Playwright end-to-end coverage of onboarding, opportunity, diff, approval and rollback; axe
accessibility review; performance evidence at 10,000 pages with dashboard p95 and queue fairness;
resilience drills for worker loss, lease expiry, duplicate events and partial provider failure; and
an independent security review of the injection denylist and secret rotation.
