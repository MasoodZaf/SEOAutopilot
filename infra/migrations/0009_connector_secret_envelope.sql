BEGIN;

ALTER TABLE connector_oauth_state
  ADD COLUMN requested_property_ref text;

CREATE TABLE connector_secret (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  connector_id uuid NOT NULL,
  provider text NOT NULL CHECK(provider IN('google_search_console')),
  ciphertext bytea NOT NULL,
  nonce bytea NOT NULL CHECK(octet_length(nonce)=12),
  aad_hash text NOT NULL CHECK(length(aad_hash)=64),
  key_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  CONSTRAINT connector_secret_connector_tenant_fk FOREIGN KEY(connector_id,tenant_id)
    REFERENCES connector(id,tenant_id)
);
CREATE UNIQUE INDEX connector_secret_active_idx ON connector_secret(tenant_id,connector_id)
  WHERE revoked_at IS NULL;

ALTER TABLE connector_secret ENABLE ROW LEVEL SECURITY;
CREATE POLICY connector_secret_tenant_isolation ON connector_secret
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
