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

**Remaining:**

- Move the worker onto the two new roles: the tenant GUC in the modules that never set it, and
  `seo_autopilot_relay` for the outbox relay and routine scheduler. The worker still runs as the
  owning superuser, so it bypasses the policies; it is not attacker-reachable, but isolation is not
  complete until it moves.
- Provision both role passwords in deployment and document the step in `DEPLOYMENT.md`.
- Move the remaining safety controls off `AsyncMock`: approvals, deployment, freeze and kill switch,
  daily change budget, mode ceiling, calibration idempotency, cursor scoping.

**Exit:** no service connects as a superuser, and every control named in `AGENTS.md` has a test that
runs against real PostgreSQL.

### H2 — Make the evidence real

- Open CodeArc to the crawler (crawler-UA allowlist or a signed bypass token) and complete a full
  500-page crawl. The current latest crawl is `cancelled`, so today's opportunity set rests on
  partial evidence.
- Resolve the opportunity yield question: TheCalcHive produced 1 opportunity from 32 pages and
  WordKit 1 from 13. Confirm the sites are clean or find the rule that is not firing.
- Run the crawl scenarios against the in-repo hostile fixture rather than only production sites.

**Exit:** a completed full crawl on CodeArc, and an opportunity count defensible per page.

### H3 — Close the change loop

- Real GitHub adapter: branch, commit, PR, drift detection against `base_hash`, idempotency key
  yielding exactly one PR. The manifest and PR-body formatter already exist.
- Post-deploy verification by re-crawling the target URL and diffing against expectation.
- Rollback with incident and audit records.

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
