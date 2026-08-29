BEGIN;

ALTER TABLE connector DROP CONSTRAINT connector_type_check;
ALTER TABLE connector ADD CONSTRAINT connector_type_check
  CHECK(type IN('google_search_console','cloudflare_dns'));

COMMIT;
