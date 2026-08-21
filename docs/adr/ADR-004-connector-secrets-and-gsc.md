# ADR-004 — Connector secrets and Search Console ingestion

- **Status:** accepted; local/test callback implemented, managed production adapter remains gated
- **Date:** 2026-08-19

## Decision

Connector rows contain only an opaque `secret_ref`. OAuth codes, access tokens, refresh tokens, and
PKCE verifiers must be written to an envelope-encrypted secret backend and must never be returned by
the API, persisted in job payloads, or logged. The Google Search Console connector requests only the
`webmasters.readonly` scope. OAuth state is random, stored only as a SHA-256 hash, bound to tenant,
site, connector, actor, requested scope, and a ten-minute expiry, and consumed once.

The callback activates a connector only after it exchanges the code server-side, verifies the
granted scopes, lists the caller's Search Console properties, and binds the selected URL-prefix or
domain property to the already verified site host. The implemented local/test adapter encrypts a
minimal token bundle with AES-256-GCM, binds ciphertext to tenant/connector/provider/key-version AAD,
and revokes the prior envelope during reauthorization. It stores only an opaque secret reference on
the connector.

`database_envelope` is rejected by configuration in staging and production. Those environments must
use `managed`, and the Google callback remains closed there until the managed KMS/secret-manager
adapter, runtime database role, refresh-token worker flow, revocation, and rotation drill are
implemented. The unauthenticated local callback derives tenant/site/actor only from the locked,
one-time state row; it accepts no tenant or redirect input.

Search Analytics is requested one day at a time with final data and bounded 25,000-row pages. A
durable cursor records day and offset after each idempotent metric upsert. Raw search queries are not
stored in the MVP: a dedicated per-environment HMAC-SHA-256 key produces `query_hash`; page URLs use
SHA-256 for matching and retain the URL as confidential evidence. Provider response bodies are not
logged.

## Consequences

- A database-only compromise exposes ciphertext rather than token plaintext, but local environment
  key compromise would also expose local credentials; production therefore requires managed keys.
- Query-level opportunity aggregation is possible without retaining readable user searches.
- Displaying query text later requires an explicit tenant policy, encrypted storage design, retention
  limit, and security review.
- Local mocks prove protocol and replay behavior, not Google account approval, quota sufficiency, or
  production readiness.
