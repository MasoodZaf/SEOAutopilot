# QA and Test Plan

## Strategy

Testing follows the risk chain: evidence correctness -> prioritization -> proposal safety -> authorization -> external effect -> verification -> measurement. Synthetic fixtures are the primary deterministic proof; provider sandboxes and design-partner checks are separate gates.

## Test layers

- **Unit:** URL normalization, rule evaluation, score factors, risk classification, state machines, policy decisions, schema parsing, redaction.
- **Contract:** OpenAPI snapshots, shared Zod/Pydantic fixtures, event versions, connector request/response adapters.
- **Integration:** PostgreSQL constraints/RLS, Redis leases, outbox delivery, migrations, object storage policies, connector fakes.
- **End-to-end:** site onboarding through measured deployment on a synthetic website and test repository/CMS.
- **Security:** tenant isolation, RBAC/ABAC, OAuth, CSRF, SSRF/redirect rebinding, webhook replay, prompt injection, secret leakage.
- **Resilience:** retries, duplicate events, worker loss, lease expiry, partial provider failures, stale revisions, rollback, kill switches.
- **Crawl concurrency:** tenant-scoped latest-run selection, duplicate-start denial, and the
  one-active-run database invariant under concurrent requests.
- **Performance:** 500/10k page crawls, top-20 query latency, queue fairness, dashboard p95, database growth.
- **Accessibility:** automated axe plus keyboard/screen-reader review of onboarding, opportunity, diff, approval, and rollback flows.
- **AI evaluation:** labeled expected findings, evidence precision/recall, unsupported-claim rate, unsafe-change escape rate, multilingual robustness, cost/latency.
- **Calibration:** frozen evidence identity, one-open-run enforcement, tenant/RBAC denial,
  idempotency-key conflicts, append-only history, and precision that excludes uncertain labels.

## Critical MVP scenarios

1. Connect a verified synthetic site, reject an unverified origin, and audit both.
2. Crawl a site containing loops, traps, redirects, huge bodies, JS rendering, robots exclusions, malformed canonical/schema, and private-IP links without escaping policy.
3. Resume a crashed crawl without duplicate observations or exceeding host budget.
4. Import GSC pages/queries across pagination and checkpoint restart without double counting.
5. Produce identical page scores and top 20 for identical evidence/scoring version.
6. Seed 25 issues; verify expected high-value issues rank in the top 20 with explanations.
7. Feed prompt-injection text in HTML, metadata, repository, and CMS content; confirm it cannot change tools, policy, or deployment.
8. Reject hallucinated evidence IDs and claim-changing output from automatic eligibility.
9. Edit proposal after validation; confirm validation and material approvals are invalidated.
10. Enforce role, tenant, separation-of-duties, mode ceiling, freeze, budget, and kill-switch denial.
11. Deploy the same idempotency key twice; create only one PR/revision.
12. Detect source drift before deploy and require refresh/revalidation.
13. Fail canary verification; halt batch, roll back where supported, and generate incident/audit records.
14. Create baseline/follow-up measurement windows without presenting association as guaranteed causation.

### Google Search Console connector gates

- Authorization starts only for an already verified tenant site and an owner/admin actor.
- Requested Google scope is exactly `webmasters.readonly`; client secret and tokens never appear in
  responses, audit metadata, outbox payloads, or logs.
- OAuth state is unpredictable, hash-only at rest, tenant/site/connector-bound, expires in ten
  minutes, is consumed once, and rejects replay or property-host mismatch.
- Search Analytics uses final data, one-day windows, 25,000-row pages, bounded page counts, and a
  durable day/offset checkpoint.
- A crash after metric upsert but before checkpoint save can replay without duplicate metric rows.
- Tenant A can read only tenant A connectors, syncs, and metrics; cross-tenant foreign-key attempts
  fail even for internally malformed jobs.
- Raw query text is absent from the metric schema and fixtures; deterministic aggregation uses a
  dedicated HMAC key that fails closed below 32 characters.
- Mock/provider contract success is local proof only, never Google approval or quota readiness.

Local callback acceptance result — 2026-08-19: callback tests reject consumed/expired or malformed
state before provider calls, reject broader scopes and unverified properties before secret storage,
and verify that audit/outbox records contain no authorization code or token. AES-256-GCM tests confirm
token strings are absent from ciphertext and round-trip only with the correct key/AAD. PostgreSQL
proof shows one active envelope for tenant A, zero visibility for tenant B, and zero plaintext token
columns. This does not qualify the managed production adapter or a live Google account.

Local calibration/crawler checkpoint — 2026-08-20: `make check` passed with 54 API, 15 worker,
17 crawler, and 1 shared-contract test; web lint/typecheck/build passed, while the web package still
has no automated test cases. Calibration unit coverage proves request-bound idempotency hashing,
summary calculations, and viewer-role denial. Crawler regressions prove visible-text render selection
and distinguish permanent poison events from temporarily leased work without acknowledging valid work
before terminal persistence. A live local recovery also verified 30-second database and Redis lease
refresh after an expired zero-observation job was auditably requeued. These
checks are local implementation evidence, not calibrated CodeArc finding accuracy or launch approval.

The crawl-control checkpoint also proves service-level duplicate denial and tenant/site-scoped latest
crawl resolution. Migration `0011_one_active_crawl.sql` was applied after confirming no duplicate
active rows, and PostgreSQL now enforces one queued/running crawl per tenant/site. The live pilot was
browser-verified to show `running`, suppress duplicate start, and suppress calibration creation until
the latest crawl and analysis finish. The corrective run later reached `partial` with 500/500 pages
browser-rendered, zero fetch errors, and terminal analysis of 500 scores and 1,495 findings.

The aggregate Search Console checkpoint proves tenant/site predicates, weighted-position and CTR
math, sparse-data signaling, cross-tenant not-found behavior, and 90-day range rejection. A focused
test asserts the aggregate statement does not select `query_hash`. Live browser proof shows 0 clicks,
11 impressions, 0.0% CTR, average position 45.9, and an explicit sparse-evidence warning for the
default CodeArc window; these values are ingestion evidence, not SEO conclusions.

The PageSpeed checkpoint adds parser tests for normalized Lighthouse score/LCP/INP/CLS/TTFB fields,
invalid-score rejection, and provider rate-limit mapping without an API key. API tests prove the
target is selected server-side from a tenant-scoped terminal crawl, cross-tenant sites are hidden,
and idempotent replay returns the original run. Migration `0012_pagespeed_observations.sql` was
applied locally. One live keyless CodeArc request was accepted and then failed closed with
`provider_rate_limited`; no observation was inserted. After enabling the API and creating an API-only
restricted local key, keyed run `09dc4976-5da1-498a-80dd-af9c50996437` completed on attempt 1 and
stored Lighthouse 13.4.1 metrics: performance 42, LCP 11,058 ms, CLS 0.017078, TTFB 2 ms, and null
lab INP. This proves one local provider round trip and normalized persistence, not field performance,
trend reliability, production secret management, quota sufficiency, or ranking effect.

## Test matrix by mode

| Behavior | Observe | Recommend | Autopilot |
|---|---:|---:|---:|
| Crawl/analyze | Yes | Yes | Yes |
| Generate diff | Finding only | Yes | Yes |
| Manual deploy after approval | No | Yes | Yes |
| Automatic low-risk deploy | No | No | Policy + gates |
| High-risk automatic deploy | Never | Never | Never |

## Data and fixtures

- `fixtures/site-small`: 30 pages for PR checks.
- `fixtures/site-500`: deterministic MVP acceptance dataset.
- `fixtures/site-hostile`: SSRF links, injection strings, crawl traps, malicious schema.
- `fixtures/gsc`, `ga4`, `pagespeed`: recorded, redacted provider payloads with schema versions.
- `fixtures/connectors`: GitHub and CMS fake servers supporting retries, drift, failure, and rollback.
- `evals/agents`: labeled English and multilingual cases with expected evidence and prohibited operations.

No real customer content or credentials are committed.

### Hostile 500-page local acceptance result — 2026-08-19

The generated `fixtures/site-hostile` case combines the 500-page deterministic dataset with robots
exclusions, excessive sitemap entries, loops, depth/query traps, private and external targets,
credential-bearing URLs, malformed canonical/JSON-LD evidence, `noindex`, and page-provided prompt
injection text. Two in-memory runs produced identical ordered observations. The focused test observed
500 pages in approximately 155–196 ms per two-run test execution on the local development machine,
with one robots skip, zero fetch errors, discovery truncation reported, and no forbidden request.

This passes the deterministic traversal/scope-containment sub-gate only. It does not measure public
network throughput, production Playwright capacity, DNS rebinding under an egress proxy, provider
approval, or the remaining GSC/performance/UI deliverables.

## Quality gates

### Pull request

- Formatting, lint, type checks, unit/contract tests, migration validation.
- Secret/dependency/SAST scans and API compatibility diff.
- Changed security boundary requires targeted negative tests.

### Main/staging

- Integration suite with PostgreSQL RLS and Redis.
- Synthetic end-to-end Recommend workflow.
- Container/IaC scans, SBOM, browser compatibility, accessibility smoke.

### Autopilot release

- Full hostile-site and AI safety evals.
- Canaried deployment and rollback drill.
- 0 prohibited automatic changes; unsafe-change upper confidence bound within approved threshold.
- Cross-tenant matrix passes for every resource/action endpoint.
- Security and operations owners sign the release gate.

## Initial service objectives

- Control plane monthly availability 99.9%.
- Non-analytical API p95 < 400 ms at target load.
- Queue-to-start p95 < 60 seconds for standard plan under normal load.
- Deployment adapter success > 99% excluding provider outages; zero duplicate effects.
- Audit event coverage 100% for enumerated sensitive transitions.

## Defect policy

- P0: cross-tenant exposure, unauthorized publish, secret exposure—kill affected capability, incident process.
- P1: data corruption, unbounded crawler, failed rollback—block release.
- P2: incorrect prioritization or degraded connector with workaround—owner and target release required.
- P3/P4: ordinary functional/visual issues—triage by impact.

Local tests prove only the tested build and fixtures. They do not prove search ranking, Google/provider approval, customer authorization, or production readiness.
