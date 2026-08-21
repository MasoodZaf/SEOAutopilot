# SEO Autopilot

An auditable, multi-tenant SEO operations platform that connects crawl, search, analytics, performance, and delivery evidence to prioritize, approve, deploy, and measure SEO improvements.

## Repository status

This is the production-oriented foundation with executable Phase 1/2 slices: fail-closed authentication, tenant-scoped site onboarding, DNS verification, durable crawl dispatch, robots/sitemap-aware crawling, PostgreSQL leases, and immutable page observations. External connectors and automatic publishing remain closed behind feature flags until their roadmap gates are approved.

## Quick start

1. Copy `.env.example` to `.env.local` and keep secrets local.
2. Run `make infra-up`.
3. Run `make install`.
4. Run `make migrate`.
5. Start the API with `.venv-api/bin/uvicorn app.main:app --app-dir services/api --reload`.
6. Start the web/crawler packages with `pnpm dev`.

If another local PostgreSQL already owns port 5432, either change the Compose host port or run migration checks inside the container. Do not repoint the application at an unrelated local database.

## Read first

Start with `MASTER_PRD.md`, then `ARCHITECTURE.md`, `SECURITY.md`, `DATA_MODEL.md`, `API_SPEC.md`, `QA_TEST_PLAN.md`, and `ROADMAP.md`. Contributors and coding agents must follow `AGENTS.md`.

## Safety defaults

`DEPLOYMENTS_ENABLED=false` and `AUTOPILOT_ENABLED=false`. LLM results are advisory and cannot bypass proposal validation, policy, approval, deployment, or verification stages.

## Competitive intent

The SerpApi Awesome SEO Tools repository is tracked as a breadth benchmark. This product aims to outperform catalogs and disconnected toolchains through a verifiable end-to-end workflow, not unsupported superiority claims.
