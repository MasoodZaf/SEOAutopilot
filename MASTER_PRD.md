# SEO Autopilot — Master Product Requirements Document

**Status:** Draft for product approval  
**Owner:** Product & Engineering  
**Last updated:** 2026-08-18  
**Target:** Multi-tenant SaaS, enterprise-ready MVP

## 1. Product thesis

SEO Autopilot continuously combines crawl evidence, Google Search Console (GSC), GA4, Lighthouse/PageSpeed, repository/CMS context, and bounded LLM analysis to identify the highest-value SEO work, prepare reviewable fixes, deploy only within tenant policy, and measure results.

The product is not a bulk-content generator and does not promise rankings. It is an auditable decision-and-delivery system for SEO work.

## 2. Outcomes

1. Connect and verify a website in under 15 minutes.
2. Crawl and score every eligible canonical page with reproducible evidence.
3. Rank the top 20 opportunities by impact, confidence, effort, and risk.
4. Produce source-linked proposals and human-readable diffs.
5. Route proposals through role- and policy-based approval.
6. Deploy via GitHub pull request or a supported CMS connector.
7. Track leading and lagging results against a frozen pre-change baseline.

## 3. Users and jobs

- **SEO lead:** prioritize work, tune policy, approve high-risk changes, evaluate impact.
- **Content editor:** review claims and copy, edit proposals, publish approved content.
- **Developer:** inspect technical diffs, approve repository changes, own rollback.
- **Executive/viewer:** see portfolio health, opportunity value, and realized outcomes.
- **Tenant administrator:** manage users, SSO, connectors, retention, and audit exports.

## 4. Modes

| Mode | Analysis | Proposal | Deployment |
|---|---|---|---|
| Observe | Yes | Findings only | Never |
| Recommend | Yes | Reviewable fixes | Explicit approval required |
| Autopilot | Yes | Policy-bounded fixes | Automatic only for allowlisted low-risk change classes; all others require approval |

Mode is configured per site. A tenant-wide ceiling can prevent sites from selecting a more permissive mode. Mode changes are audited and require an Owner or Admin.

## 5. Core agents

All agents emit structured findings; none deploy directly.

- **Technical SEO Agent:** crawlability, indexability, canonicals, redirects, status codes, sitemap/robots, structured data, rendering, Core Web Vitals signals.
- **Content SEO Agent:** title/H1 alignment, intent coverage, duplication, freshness, thin content, claim/evidence quality.
- **Keyword Opportunity Agent:** GSC query/page opportunities, striking-distance terms, CTR gaps, cannibalization, decay.
- **Internal Linking Agent:** orphan pages, link equity, contextual anchor candidates, broken links.
- **SEO Content Agent:** bounded briefs and proposed edits grounded in approved sources and brand rules.
- **SEO Performance Agent:** Lighthouse/PageSpeed trends, regressions, template-level performance patterns.
- **GEO/AI Search Visibility Agent:** answerability, entity clarity, citation-ready facts, provenance, and structured content for AI-assisted discovery. It must not claim or simulate ranking in an AI answer engine.

## 6. MVP journey

1. User creates an organization and site.
2. User proves site control using GSC property access, GitHub/CMS access, or a verification token.
3. System performs a safe crawl honoring robots, tenant scope, rate limits, and SSRF protections.
4. User connects GSC; GA4 and PageSpeed are optional enrichments.
5. System normalizes page, crawl, query, and performance observations.
6. Scoring engine assigns page health and opportunity scores with factor explanations.
7. System presents the top 20 opportunities with evidence and confidence.
8. User opens a proposal, edits it, reviews validation results, and approves or rejects it.
9. Deployment adapter opens a GitHub PR or stages/publishes a CMS change according to policy.
10. System stores the deployment receipt, monitors the changed URLs, and compares outcomes to the baseline.

## 7. Functional requirements

### Onboarding and tenancy

- Organizations, sites, environments, members, roles, and tenant-scoped settings.
- Roles: Owner, Admin, SEO Manager, Editor, Developer, Viewer, Service Account.
- SSO/SAML and SCIM are post-MVP enterprise capabilities; the authorization model must support them from day one.
- Every tenant-owned row carries `tenant_id`; database access is tenant-scoped and defense-in-depth RLS-ready.

### Crawl and ingestion

- Seed from homepage and sitemap; include/exclude path rules; canonical URL normalization.
- Render with Playwright only when required; default to HTTP fetch to reduce cost.
- Respect robots.txt by default. Any authenticated/private crawl requires explicit configuration and a separate credential boundary.
- Record request/response metadata, rendered DOM-derived signals, link graph, content hash, and evidence timestamps.
- Ingest GSC Search Analytics by date/page/query/device/country within API limits; store immutable sync checkpoints.
- Ingest GA4 landing-page metrics and PageSpeed/Lighthouse lab/field data when authorized.

### Scoring and opportunities

- Page health score: 0–100 with versioned factor weights and missing-data handling.
- Opportunity score: `impact × confidence × urgency ÷ effort`, adjusted by risk and data quality.
- Deduplicate correlated findings and group template-wide issues.
- Freeze the score version and evidence references on each opportunity.
- Top 20 must be deterministic for a fixed dataset and scoring version.

### Proposals and approvals

- Proposal contains rationale, evidence, affected URLs/files, before/after diff, predicted mechanism, risk class, validations, and rollback plan.
- Risk classes: low, medium, high, prohibited. Initial low-risk allowlist: metadata edits, internal links, alt text, and schema corrections that pass validation and do not alter claims.
- Two-person approval may be required by tenant policy. The author cannot satisfy separation-of-duties rules.
- Content containing regulated, legal, medical, financial, or unverified factual claims is never auto-published.

### Deployment and measurement

- GitHub: create branch, commit signed machine-readable manifest, open PR, observe merge/close status.
- CMS: stage first when supported; retain revision/version ID; verify post-publish rendering.
- Idempotency keys prevent duplicate deployment.
- Detect out-of-band edits and require rebase/revalidation before deployment.
- Measurement window defaults to 28 days before and after, with annotation for seasonality and site-wide releases; report association, not guaranteed causation.

## 8. Non-functional requirements

- Availability target: 99.9% for control plane; asynchronous jobs are resumable and idempotent.
- API p95 under 400 ms for non-analytical reads; crawl throughput is tenant- and host-limited.
- Encryption in transit and at rest; secrets in a managed secret store outside local development.
- Immutable audit events retained per contract, default 365 days.
- Regional data residency and configurable retention are enterprise roadmap items.
- Accessibility target: WCAG 2.2 AA for primary workflows.
- Observability: structured logs, traces, metrics, job lineage, connector health, and cost attribution by tenant/site.

## 9. MVP acceptance criteria

- A verified user can connect one site and complete a 500-page crawl.
- GSC OAuth connects with least-privilege scopes and a resumable 16-month backfill plan.
- Each crawled page has a score, factors, evidence timestamp, and scoring version.
- Top 20 ranking is reproducible and excludes suppressed/prohibited opportunities.
- A proposal can be edited, validated, approved, rejected, and expired.
- GitHub deployment opens a PR with exact diff and audit manifest; CMS deployment can remain staged behind a feature flag until its connector passes certification.
- Every state transition appears in a tenant-filtered audit trail.
- A merged/published change creates a measurement baseline and scheduled follow-up.
- Cross-tenant isolation, SSRF, authorization, idempotency, and rollback tests pass.

## 10. Product gates

| Gate | Evidence required | Authority |
|---|---|---|
| G0 Product approval | PRD, architecture, roadmap accepted | Product owner |
| G1 Engineering ready | Threat model, API/data contracts, migration and rollback tested | Tech lead + Security |
| G2 Internal alpha | Synthetic site end-to-end; no critical/high security findings | Product + Engineering |
| G3 Design partner | Connector consent, DPA/retention choices, named approvers | Customer admin + Product |
| G4 Recommend GA | SLOs met; audit export and support runbook ready | Operations + Security |
| G5 Autopilot beta | Low-risk policy corpus, canary, kill switch, rollback drills | Change advisory owner |
| G6 Autopilot GA | Measured reliability over beta cohort; independent security review | Executive release authority |

Technical completion or successful local tests do not satisfy customer, provider, security, or launch approval gates.

## 11. Success metrics

- Activation: verified site + first completed crawl + GSC connected.
- Time to first valuable opportunity; proposal acceptance rate; median approval latency.
- Deployment success and rollback rates; escaped-change defect rate.
- Percentage of opportunities with complete evidence and outcome measurement.
- Directional organic clicks, qualified sessions, CTR, position distribution, and indexed-page health—reported with confidence and caveats.
- Guardrails: no cross-tenant access, no prohibited auto-deploys, connector error budget, crawl complaint rate, LLM cost per evaluated URL.

## 12. Non-goals for MVP

- Guaranteed rankings, automated backlink outreach, review manipulation, cloaking, or search-engine policy circumvention.
- Unlimited website crawling, arbitrary browser automation, or execution of page-provided instructions.
- Autonomous publishing of net-new long-form content.
- Direct use of Google's Indexing API for ordinary pages; that API is limited to supported job/broadcast use cases.
- Replacing analytics attribution, legal review, brand review, or change-management ownership.

## 13. Open decisions before G0

- First CMS connector: WordPress REST or headless Contentful/Sanity.
- Identity provider for MVP and enterprise SSO phase.
- Initial scoring weights and the minimum evidence threshold for each opportunity class.
- Regions and retention commitments for the first design partners.
- Whether CMS publishing ships in MVP or begins as staging-only.

## 14. Competitive benchmark: SerpApi Awesome SEO Tools

SerpApi's `awesome-seo-tools` repository is a strong discovery surface: a maintained, category-based catalog spanning all-in-one suites, keyword research, content, rank tracking, technical SEO, local SEO, analytics, browser extensions, and validators. We treat its category coverage as a market checklist, while competing on workflow depth rather than list size.

Our advantage must be demonstrated, not asserted:

| Dimension | Catalog benchmark | SEO Autopilot release bar |
|---|---|---|
| Discovery | Links users to many tools | One connected evidence graph across crawl, GSC, GA4, performance, repo/CMS |
| Prioritization | User evaluates options | Reproducible top-20 ranking with factor-level explanations |
| Action | User moves between tools | Reviewable diff, validation, approval, PR/CMS staging, and rollback |
| Governance | Repository contribution history | Tenant RBAC, separation of duties, policy versions, immutable audit |
| Learning loop | No site outcome loop | Frozen baseline, deployment receipt, follow-up windows, result annotation |
| AI safety | Not its catalog purpose | Schema-bound agents, hostile-content isolation, evidence citations, no direct deploy |
| Extensibility | Add another catalog entry | Versioned provider/connector/agent SDK contracts and certification tests |

The MVP is not considered competitively complete merely because it contains the same feature labels. It must pass an end-to-end benchmark: connect a synthetic site, identify a seeded issue in the top 20, produce the correct bounded diff, enforce the required approval, deploy to a test PR/staging CMS, verify the rendered result, and show the measurement plan with a complete audit trail.
