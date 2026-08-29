# Security and Safety Model

## Objectives

Protect tenant isolation, connector authority, unpublished content, analytics/search data, and the integrity of automated changes. The system must remain safe when users, crawled pages, LLM output, webhooks, and third-party APIs are malicious or incorrect.

## Trust boundaries

1. Browser to control plane.
2. API/worker to PostgreSQL, Redis, object storage, and secret manager.
3. Crawler to arbitrary but tenant-verified public sites.
4. Connector adapters to Google, GitHub, and CMS providers.
5. Agent orchestrator to LLM providers.
6. Deployment service to customer-controlled production content/code.

## Principal threats and controls

| Threat | Required controls |
|---|---|
| Cross-tenant data access | Server-derived tenant context, scoped repositories, composite foreign keys, PostgreSQL RLS, cache/object prefixes, isolation tests |
| Broken authorization | OIDC validation, action/resource policy engine, re-authorization in workers, separation of duties, short-lived service scopes |
| SSRF / crawler escape | Verified origins, scheme/port allowlist, DNS resolution and re-check on redirects, private/link-local/metadata IP denial, egress proxy, size/time budgets |
| Prompt injection | Crawled/repository/CMS content marked untrusted, no instruction inheritance, minimal evidence, schema output, citation validation, no direct tools or deploy |
| Malicious LLM output | Strict schema, deterministic validators, allowlisted operations, diff bounds, risk classifier, approval policy, canary, kill switch |
| OAuth theft/confusion | PKCE, state/nonce, exact redirect URIs, least scopes, encrypted tokens, token rotation/revocation, external resource mapping |
| Webhook spoof/replay | Raw-body signature verification, timestamp window, delivery-ID dedupe, provider IP controls where supported |
| Supply-chain compromise | Lockfiles, provenance/SBOM, dependency scanning, pinned CI actions/images, signed releases, minimal images |
| Sensitive logging | Structured allowlist logging, field redaction, query/content classification, secret scanning, restricted traces/artifacts |
| Unsafe publication | Change manifest, source compare-and-set, validation suite, policy approval, branch protection/staging, post-deploy verification, rollback |

### PageSpeed provider boundary

PageSpeed targets are never accepted from a browser request. The API selects a page from a verified
tenant/site's latest terminal crawl, and the worker independently compares the frozen URL host with
the verified site before egress. Runs are tenant scoped, idempotent, single-active, leased, and
attempt-bounded. Provider bodies are streamed with a 2 MB cap, parsed into bounded numeric fields,
and discarded. HTTP client request logging is disabled because Google API keys may appear in query
parameters. Keys remain worker-only secrets and are never stored in a run, observation, audit event,
outbox payload, or API response. Provider failures become allowlisted safe error codes.

## Data classification

- **Restricted:** OAuth tokens, API keys, session material, private crawl credentials. Secret manager only; never prompts/logs/database plaintext.
- **Confidential:** unpublished content, GSC queries, GA4 dimensions, repository/CMS content, user identity. Encrypted, least-access, retention-controlled.
- **Internal:** scores, proposals, audit metadata, operational metrics.
- **Public:** already public site pages and explicitly published reports.

Publicly reachable does not mean safe to execute or unrestricted to retain.

## Authentication and authorization

- OIDC Authorization Code + PKCE; secure, HttpOnly, SameSite cookies for web sessions; CSRF protection on mutations.
- Validate issuer, audience, signature, expiry, nonce, and authorized redirect path.
- MFA required for owners/admins and production deployers in enterprise mode.
- Roles provide defaults; policies decide specific actions using tenant/site/environment/risk/connector context.
- Mode elevation, connector authorization, policy activation, approval, deployment, rollback, audit export, and membership changes are audited.

## Automatic-change safety

The deployment service accepts only a validated `ChangeManifest`. Initial automatic allowlist is narrow and disabled by default. A proposal is deployable only when:

1. the site is verified and active;
2. effective tenant/site mode permits the operation;
3. change class is allowlisted and not prohibited;
4. evidence, proposal, validation, policy, and source revisions still match;
5. required distinct approvals are present and unexpired;
6. per-run URL/file/line/content limits and daily budget pass;
7. freeze window, provider health, and global/tenant kill switches permit deployment;
8. rollback method and post-deploy checks are available.

Failure is closed: no partial “best effort” publication. Batch changes use canaries; verification failure halts remaining items and triggers the configured rollback/incident path.

## Crawler policy

- Default unauthenticated crawl honors robots.txt, rate limits, canonical scope, and explicit exclusions.
- No filesystem URLs, browser extension schemes, raw IP targets, nonstandard ports by default, local/private addresses, cloud metadata endpoints, or credential-bearing URLs.
- Disable downloads, dialogs, service workers, persistent browser state, and unnecessary browser permissions.
- Browser runs as non-root in a sandboxed container with read-only filesystem and constrained CPU/memory/pids.
- Limit redirects, depth, response bytes, DOM size, execution time, and requests per page. Strip credentials on cross-origin redirects.

## LLM and AI governance

- Provider selection respects tenant region/retention policy; zero-retention or equivalent enterprise terms are required for confidential data.
- Prompts and results are versioned and hashed. Store only the minimum text required; redact secrets and unnecessary PII.
- Structured outputs reference evidence IDs. Unsupported factual claims fail validation or require explicit editorial review.
- No model may modify policy, assign itself permissions, approve its own output, or claim deployment success.
- Maintain evaluation sets for injection resistance, hallucinated evidence, unsafe diffs, multilingual content, and model-version drift.

## Cryptography and secrets

- TLS 1.2+ externally; managed encryption at rest; envelope encryption with KMS for connector secrets.
- Separate keys by environment and, where available, region; rotate and test revocation.
- Local `.env.local` is ignored and for development only. Production secrets are injected at runtime.
- Signed webhook secrets and deploy credentials never enter the LLM or crawler environment.
- DNS-provider verification uses a separate connector consent followed by a separate record-creation
  consent. Manual TXT verification works for every provider; optional adapters accept only an exact
  site/zone binding and can create only the active server-derived TXT challenge. They cannot list,
  edit, or delete arbitrary records through the product workflow.

## Calibration review safety

- Calibration items snapshot server-selected opportunity and crawl evidence; clients cannot supply
  tenant, site, opportunity, page, rule, or evidence identities.
- Review notes are bounded untrusted text and never become prompts, executable instructions,
  approval reasons, or deployment input without a later policy-controlled transition.
- Reviews are append-only, actor-bound, idempotent, and audited. A review cannot change site mode,
  opportunity status, proposal status, or any external system.
- Precision uses only decided true/false-positive labels; uncertain labels remain visible but are
  excluded from the denominator.

## Crawl concurrency safety

- Latest-crawl reads resolve the site and crawl inside authenticated tenant scope; client tenant IDs
  and crawl ownership claims are ignored.
- A partial PostgreSQL unique index permits at most one queued/running crawl per tenant/site. The API
  also returns a stable conflict before enqueueing when an active run already exists.
- The dashboard derives active status from the server and removes the duplicate-start control, but
  database/API enforcement remains authoritative if a client bypasses the UI.
- Search Console query dimensions are confidential. The MVP persists only a dedicated-key HMAC for
  query aggregation; the key is distinct from cursor/session keys and raw queries are not logged or
  stored.
- The search-performance API selects only aggregate expressions and never serializes query text,
  query hashes, page URLs, countries, devices, or source connector records. Date windows are bounded
  to 90 days and site existence is resolved inside tenant scope before metric access.
- Local/test connector tokens use AES-256-GCM with tenant/connector/provider/key-version AAD and one
  active envelope per connector. Staging/production reject the database-envelope backend and require
  a managed secret store; no local callback proof qualifies that production boundary.

## Audit and incident response

Security-relevant events form an append-only hash-linked sequence per tenant and are exported to protected storage. Hash chaining detects tampering; it does not replace restricted database permissions or external retention controls.

Incident runbooks cover credential exposure, cross-tenant access, unauthorized publish, crawler abuse, provider compromise, and model regression. A global deployment kill switch is available without redeploy. Recovery evidence includes affected tenants/resources, revocations, rollback status, and post-incident tests.

## Security gates

- Threat model reviewed before design-partner data.
- SAST, dependency, secret, container, IaC, and license checks on CI.
- Cross-tenant and authorization negative tests mandatory.
- External penetration test before Autopilot GA.
- No open critical/high findings at release; medium findings require owner and dated remediation.
- Connector scope/terms and customer authorization are verified; local integration success is not provider approval.

## Current release-gate status (2026-08-28)

- External deployment is disabled by default. The API accepts only the development/test mock adapter,
  and still requires an allowed role, Recommend/Autopilot mode, no emergency or scheduled freeze, and
  remaining daily budget.
- GitHub, Shopify, WordPress, live post-deploy verification, and external rollback are fail-closed
  contracts, not working provider integrations. The control plane does not offer those actions.
- Outcome calculation requires a verified deployment record and a completed 28-day follow-up window.
  It reports empirical association and must not be described as causal evidence.
- Tenant tables have RLS policies, but the local Compose application role is also the table-owning
  PostgreSQL role and migrations do not use `FORCE ROW LEVEL SECURITY`. A least-privileged non-owner
  runtime role plus live cross-tenant denial proof is mandatory before staging or production.
- Proposal and suppression actor IDs are server-derived audit UUIDs, but there is no durable
  tenant-membership table or database foreign key yet. Production OIDC plus membership resolution
  and referential proof remain an identity release gate.
