# ADR-001: Trusted tenant context and database isolation

**Status:** Accepted for Phase 1  
**Date:** 2026-08-18

## Decision

The API derives `TenantContext` from a verified OIDC identity and server-side membership lookup. Until that verifier is configured, business endpoints fail closed with `401`; tenant authority is never accepted from a public header or request body.

Every tenant database transaction calls PostgreSQL `set_config('app.tenant_id', tenant_id, true)` and every repository query also includes an explicit tenant predicate. PostgreSQL RLS provides defense in depth. Tests override the dependency directly; there is no development header backdoor.

## Consequences

- Background workers must construct context from signed job metadata and re-authorize effects.
- The production application role must not own tables or have `BYPASSRLS`/superuser privileges.
- Connection pooling is safe only because tenant configuration is transaction-local.
- Missing context returns no tenant rows under RLS and application endpoints fail before opening a tenant transaction.

## Rollback

The application can revert to fail-closed read-only behavior by removing site routes. RLS policies remain in place; removing them requires a separately reviewed migration.
