BEGIN;

-- Non-secret configuration for a connector, kept beside the credential rather
-- than inside it.
--
-- A GitHub connector needs three things a deployment cannot be performed
-- without: which repository, which base branch, and how a crawled URL path maps
-- to a file in that repository. None of them is a secret, and putting them in
-- the encrypted payload would mean decrypting a credential just to render a
-- settings page. They also differ per site, which is the whole point: they are
-- currently install-wide settings, so two tenants would share one repository
-- and one token.
--
-- Existing rows get an empty object, so every connector already stored keeps
-- behaving exactly as it did.
ALTER TABLE connector
  ADD COLUMN IF NOT EXISTS config_json jsonb NOT NULL DEFAULT '{}'::jsonb;

-- The type list and the provider-key rule both predate GitHub, and the existing
-- CHECK constraints refuse the new connector outright. Widening them is the
-- point at which the database is told what a GitHub connector is allowed to be.
ALTER TABLE connector DROP CONSTRAINT IF EXISTS connector_type_check;
ALTER TABLE connector ADD CONSTRAINT connector_type_check
  CHECK(type IN('google_search_console','dns_provider','github_repository'));

-- A provider key says *which* provider, so it belongs to every connector type
-- that has more than one. Search Console has exactly one and keeps null.
ALTER TABLE connector DROP CONSTRAINT IF EXISTS connector_dns_provider_key_check;
ALTER TABLE connector DROP CONSTRAINT IF EXISTS connector_provider_key_check;
ALTER TABLE connector ADD CONSTRAINT connector_provider_key_check
  CHECK((type IN('dns_provider','github_repository') AND provider_key IS NOT NULL)
        OR (type NOT IN('dns_provider','github_repository') AND provider_key IS NULL));

ALTER TABLE connector DROP CONSTRAINT IF EXISTS connector_github_provider_check;
ALTER TABLE connector ADD CONSTRAINT connector_github_provider_check
  CHECK(type <> 'github_repository' OR provider_key IN('github_app','github_pat'));

-- An app installation has no credential to store: the token is minted per
-- deployment and never written down. Anything that put one here would be a bug
-- worth failing the transaction over, so the rule lives where it cannot be
-- forgotten rather than in the one service that currently honours it.
ALTER TABLE connector DROP CONSTRAINT IF EXISTS connector_github_app_no_secret_check;
ALTER TABLE connector ADD CONSTRAINT connector_github_app_no_secret_check
  CHECK(provider_key IS DISTINCT FROM 'github_app' OR secret_ref IS NULL);

-- The mirror of it: a stored-token connector reporting active with nothing
-- stored would fail on the first approved change.
ALTER TABLE connector DROP CONSTRAINT IF EXISTS connector_github_pat_has_secret_check;
ALTER TABLE connector ADD CONSTRAINT connector_github_pat_has_secret_check
  CHECK(provider_key IS DISTINCT FROM 'github_pat'
        OR status <> 'active'
        OR secret_ref IS NOT NULL);

-- The provider column on a stored secret has said 'google_search_console' and
-- nothing else since 0009, and no migration since has widened it. That is not
-- only a blocker for GitHub: the DNS provider connector added in 0019 writes
-- 'dns_provider:cloudflare', so `connect_dns_provider` cannot have succeeded
-- against a real database at any point. Its tests drive a mocked session, where
-- a CHECK constraint does not exist.
--
-- An allow-list of families rather than an exact list, so a second DNS provider
-- does not need a migration to be storable, while anything unrecognised is
-- still refused.
ALTER TABLE connector_secret DROP CONSTRAINT IF EXISTS connector_secret_provider_check;
ALTER TABLE connector_secret ADD CONSTRAINT connector_secret_provider_check
  CHECK(provider IN('google_search_console','github_pat')
        OR provider LIKE 'dns_provider:%');

COMMIT;
