# ADR-006: Scoring Version Governance

- **Status:** Provisional implementation; product calibration not approved
- **Date:** 2026-08-19

## Decision

Deterministic rules run before any LLM workflow. Every analysis run references a named immutable
scoring version, evidence cutoff, and request hash. Page scores, findings, and opportunities retain
that version and direct crawl/page-observation evidence references.

`technical-v1` is the initial engineering baseline. It deducts page-health points by finding
severity and ranks each technical opportunity using explicit impact, confidence, urgency, effort,
and risk factors. Fixed input rows and the same version must produce identical scores and ordering.
The database tie-break is `score DESC, fingerprint ASC, id ASC`.

Reprocessing the same crawl/version/request hash returns the completed analysis run. Finding and
opportunity fingerprints are stable per page, rule, and version; a later crawl resolves absent open
findings and expires their still-open opportunities before reopening current evidence.

## Safety consequences

- LLMs cannot create evidence, set score factors, suppress opportunities, or change scoring versions.
- Prohibited opportunities receive no ranking authority and are excluded from top lists.
- Canonical and robots-related recommendations receive a risk penalty and remain subject to the
  separate proposal, validation, approval, and deployment policy lifecycle.
- Activating a successor version requires benchmark evaluation, named approval, and a new immutable
  row. Prior outputs are never silently rewritten.

## Compatibility and rollback

Migration `0005_scoring_and_opportunities.sql` is expand-only. Application rollback can stop the
analysis consumer and ignore the new tables while the prior crawl/page API remains available. Do not
drop populated analysis tables during rollback; correct defects with a successor scoring version or
forward migration. Before a fresh migration rehearsal, verify duplicate page/crawl observations and
link edges are absent because the migration adds uniqueness constraints.

## Open calibration gate

`technical-v1` is locally verified engineering behavior, not an approved prediction of SEO impact.
The Phase 3 gate still requires seeded benchmark thresholds, representative site review, injection
evaluation, and product approval of weights and minimum evidence levels.
