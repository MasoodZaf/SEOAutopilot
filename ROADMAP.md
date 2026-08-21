# Roadmap

## Phase 0 — Product and risk closure (2 weeks)

**Deliverables:** approved PRD, ADRs, threat model, scoring rubric v0, UX flows, design-partner profile, first CMS decision, provider scope review, synthetic fixture spec.

**Exit:** G0 approved; no unresolved decision that would change tenancy, identity, connector authority, or deployment semantics.

## Phase 1 — Platform foundation (3 weeks)

- Monorepo, CI, environments, identity/tenant context, RBAC skeleton.
- PostgreSQL migrations/RLS, Redis job/outbox pattern, audit events.
- Site CRUD, verification challenge, connector credential boundary.
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

## Delivery principles

- Ship vertical slices: evidence -> opportunity -> proposal -> deploy -> measure.
- Feature flags default closed for external writes.
- Provider feasibility, account creation, successful local mocks, and prepared documents are not launch approval.
- Autopilot scope expands only after policy simulation and production evidence; never by default.
