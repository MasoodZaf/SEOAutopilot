# SEO Autopilot

An auditable, multi-tenant SEO operations platform that connects crawl, search, analytics, performance, and delivery evidence to prioritize, approve, deploy, and measure SEO improvements.

---

## 🏗️ Architecture & Services

- **`apps/web`**: Next.js 15 App Router control plane with Proposal Review Studio, Kill-Switch Center, and Measurement Dashboards.
- **`services/api`**: FastAPI API with PostgreSQL Row-Level Security, multi-tenant Auth, Governance, and Measurement engines.
- **`services/worker`**: Asynchronous Python worker orchestrator with 6-agent rules engine, LLM defenses, and Outbox CDC relay.
- **`services/crawler`**: Adaptive Playwright & HTTP crawler with SSRF blocking and hostile fixture containment.
- **`packages/contracts`**: Shared TypeScript Zod validation schemas.
- **`infra/migrations`**: 16 PostgreSQL migrations (`0001` to `0016`).

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

- **Row-Level Security (RLS)**: Enforced across 100% of database tables with `app.tenant_id` session scoping.
- **8-Stage Change Lifecycle**: `Finding -> Opportunity -> Proposal -> Validation -> Approval -> Deployment -> Verification -> Measurement`.
- **Separation of Duties**: Change authors cannot approve their own proposals. Medium/high risk proposals mandate two-person approval.
- **Drift Protection**: Deployment halts if live content has diverged from the proposal's SHA-256 base hash.
- **Emergency Kill-Switch**: Real-time site freeze (`emergency_freeze`) halts all automated deployments instantly.
- **Statistical Integrity**: 28-day baseline vs follow-up windows with Synthetic Control groups (Difference-in-Differences causal inference).

---

## 🧪 Verification & Test Suites

Run the complete verification gate across TypeScript and Python services:
```bash
make check
```
- **134 / 134 automated tests passing** (FastAPI, Worker, Crawler, Contracts, Web).
- **0 errors / 0 warnings** on Ruff, Pyright, and ESLint.
