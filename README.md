# SEO Autopilot

An auditable, multi-tenant SEO operations platform that connects crawl, search, and lab-performance evidence to prioritize opportunities and prepare governed changes. External deployment and rollback connectors are intentionally blocked until certified.

## Product deployment model

SEO Autopilot is deployed as a standalone hosted control-plane application. Customer websites do not embed its dashboard or run privileged credentials. A verified site connects read-only evidence sources to the control plane; after a human approves an exact validated change, a certified connector opens a GitHub pull request or creates a staged CMS revision. Direct publication remains disabled until connector, rollback, and policy certification pass.

---

## 🏗️ Architecture & Services

- **`apps/web`**: Next.js 16 App Router control plane with evidence review, governance, and outcome tracking.
- **`services/api`**: FastAPI API with PostgreSQL Row-Level Security, multi-tenant Auth, Governance, and Measurement engines.
- **`services/worker`**: Asynchronous Python worker with a six-dimension deterministic rules engine and durable event consumers. LLM and external deployment adapters are release-gated.
- **`services/crawler`**: Adaptive Playwright & HTTP crawler with SSRF blocking and hostile fixture containment.
- **`packages/contracts`**: Shared TypeScript Zod validation schemas.
- **`infra/migrations`**: 26 ordered PostgreSQL migrations (`0001` to `0026`).

---

## 🚀 Quick Start

1. **Environment**:
   ```bash
   cp .env.example .env.local
   ```
2. **Infrastructure**:
   ```bash
   make infra-up
   ```
3. **Install Dependencies**:
   ```bash
   make install
   ```
4. **Run Migrations**:
   ```bash
   make migrate
   ```
5. **Run Verification Gate**:
   ```bash
   make check
   ```

---

## 🛡️ Enterprise Safety & Governance

- **Tenant isolation**: Tenant filters and RLS policies exist across tenant-owned tables. The local Compose role owns the tables, so non-owner runtime-role proof remains a production release gate.
- **8-Stage Change Lifecycle**: `Finding -> Opportunity -> Proposal -> Validation -> Approval -> Deployment -> Verification -> Measurement`.
- **Separation of Duties**: Change authors cannot approve their own proposals. Medium/high risk proposals mandate two-person approval.
- **Drift Protection**: The development mock deployment path halts when supplied live content diverges from the proposal base hash.
- **Emergency Freeze**: Site freeze, global deployment flag, mode, freeze-window, role, and daily-budget checks fail closed before any adapter is invoked.
- **Measurement Integrity**: Measurement requires independently verified deployment evidence and a complete 28-day follow-up window. Results are association, not causal attribution.
- **Connector status**: GitHub, Shopify, WordPress, live verification, and external rollback adapters do not report success; they remain blocked pending real provider integration and certification.

---

## 🤖 Always-on workflows

- **Scheduled routines**: deterministic daily/weekly/monthly slots for site audits, keyword
  reclustering, sitemap coverage, content briefs, competitor scans, answer-engine readiness, and the
  weekly report. Claiming is idempotent across worker replicas and backlog is collapsed after an
  outage rather than replayed. Behind `ROUTINES_ENABLED`, default off.
- **Keyword workspace**: Search Console query terms are sealed per site in an AES-256-GCM envelope
  alongside the existing HMAC, which enables deterministic token-overlap clustering, rule-based
  intent, question-share answer-engine candidacy, striking-distance and cannibalisation signals.
  Readable terms are available only through one role-gated, audited endpoint.
- **Content briefs and refresh queue**: every gap a brief names is traced to a stored observation.
  A brief is advisory: it carries no diff and no deployment authority.
- **Sitemap coverage**: declared versus crawled versus indexable, measured within one crawl.
- **Competitors**: only URLs a human put on record, fetched under a shared egress policy, the
  competitor's robots.txt, and per-hop redirect revalidation. Structure only, never body text.
- **AI search visibility**: readiness measured from first-party evidence. It does not observe
  answer-engine citations; that needs a certified provider and is not integrated.
- **Agent workspace**: a Chat / Tasks / Skills / Reports console over twelve skills. Routing is
  deterministic against a code-defined registry and re-checks the actor's role, so chat grants no
  authority the actor did not already have and no skill can deploy or approve.
- **Report delivery**: weekly digests to https-only, public-address-checked webhooks, carrying
  headline counts rather than page-level evidence. Behind `NOTIFICATIONS_ENABLED`, default off.

---

## 🧪 Verification & Test Suites

Run the complete verification gate across TypeScript and Python services:
```bash
make check
```
- The authoritative current result is the output of `make check`; do not copy a historical count into release claims.
- The Next.js production build is also required. Focused web unit tests cover the owned-site allowlist and fail-closed advisory copy; an automated browser end-to-end suite remains a release gap.
