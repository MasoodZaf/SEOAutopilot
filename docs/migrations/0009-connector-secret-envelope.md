# Migration 0009 — connector secret envelope

## Compatibility

This is an expand-only migration. It adds nullable `requested_property_ref` to existing OAuth state
rows and creates `connector_secret`; it does not rewrite or delete connector data. Pre-migration
pending state lacks a property binding and is deliberately rejected by the callback, so users must
restart authorization.

The authorization endpoint now requires `property_ref`. This connector API had not passed an alpha
compatibility gate; clients in this repository must update atomically with the API.

## Roll forward

1. Apply `0009_connector_secret_envelope.sql`.
2. Deploy API code with `GOOGLE_CONNECTORS_ENABLED=false`.
3. Configure a fresh local/test AES key and enable only in a non-production environment.
4. Verify callback replay, property binding, ciphertext, RLS, and reauthorization rotation.

## Rollback and incident response

Disable `GOOGLE_CONNECTORS_ENABLED` first. Revoke the corresponding Google grants, mark connector
rows revoked, and securely destroy the affected encryption key. Application rollback can leave the
new nullable column and table unused. Dropping either is a later contract migration only after all
secret references are revoked and no connector points to `db-envelope://`; ciphertext deletion is
irreversible and is not used as the first rollback action.
