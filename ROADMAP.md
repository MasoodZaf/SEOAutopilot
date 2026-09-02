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
- Still open: competitor and answer-engine visibility need a provider decision on terms and
  economics before any external SERP data is fetched.

## Delivery principles

- Ship vertical slices: evidence -> opportunity -> proposal -> deploy -> measure.
- Feature flags default closed for external writes.
- Provider feasibility, account creation, successful local mocks, and prepared documents are not launch approval.
- Autopilot scope expands only after policy simulation and production evidence; never by default.
