-- A GA4 connector, per site, like every other credential.
--
-- Search Console tells us what a page ranks for and where. It says nothing
-- about what happens after the click, so the product could rank a page and
-- never know whether anyone used it. GA4 is the other half of that.
--
-- The constraints are widened rather than relaxed. `connector_secret.provider`
-- has been the source of one shipped defect already -- migration 0009 listed a
-- single value and the DNS connector wrote a different one for ten migrations
-- without anything catching it -- so the new label is added to the same closed
-- set, and the integration test that reads this constraint back is what keeps
-- the code and the column agreeing.

ALTER TABLE connector DROP CONSTRAINT connector_type_check;
ALTER TABLE connector ADD CONSTRAINT connector_type_check
  CHECK(type IN('google_search_console','google_analytics','dns_provider','github_repository'));

-- Unchanged in effect, restated so the type list above is the only place the
-- set of connector types is written down.
ALTER TABLE connector DROP CONSTRAINT connector_provider_key_check;
ALTER TABLE connector ADD CONSTRAINT connector_provider_key_check
  CHECK((type IN('dns_provider','github_repository') AND provider_key IS NOT NULL)
        OR (type NOT IN('dns_provider','github_repository') AND provider_key IS NULL));

ALTER TABLE connector_secret DROP CONSTRAINT connector_secret_provider_check;
ALTER TABLE connector_secret ADD CONSTRAINT connector_secret_provider_check
  CHECK(provider IN('google_search_console','google_analytics','github_pat')
        OR provider LIKE 'dns_provider:%');
