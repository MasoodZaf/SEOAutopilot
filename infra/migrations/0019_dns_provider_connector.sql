BEGIN;

ALTER TABLE connector ADD COLUMN provider_key text;
UPDATE connector SET type = 'dns_provider', provider_key = 'cloudflare'
  WHERE type = 'cloudflare_dns';
ALTER TABLE connector DROP CONSTRAINT connector_type_check;
ALTER TABLE connector ADD CONSTRAINT connector_type_check
  CHECK(type IN('google_search_console','dns_provider'));
ALTER TABLE connector ADD CONSTRAINT connector_dns_provider_key_check
  CHECK((type = 'dns_provider' AND provider_key IS NOT NULL) OR (type <> 'dns_provider' AND provider_key IS NULL));

COMMIT;
