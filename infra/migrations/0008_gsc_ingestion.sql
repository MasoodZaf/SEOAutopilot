BEGIN;

ALTER TABLE site ADD CONSTRAINT site_id_tenant_unique UNIQUE(id,tenant_id);
ALTER TABLE page ADD CONSTRAINT page_id_tenant_unique UNIQUE(id,tenant_id);

CREATE TABLE connector (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL,
  type text NOT NULL CHECK(type IN('google_search_console')),
  status text NOT NULL DEFAULT 'pending_authorization'
    CHECK(status IN('pending_authorization','active','reauthorization_required','revoked','error')),
  external_account_ref text,
  secret_ref text,
  granted_scopes text[] NOT NULL DEFAULT '{}',
  consented_by uuid,
  consented_at timestamptz,
  last_sync_at timestamptz,
  token_expires_at timestamptz,
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT connector_site_tenant_fk FOREIGN KEY(site_id,tenant_id) REFERENCES site(id,tenant_id),
  UNIQUE(id,tenant_id),
  UNIQUE(tenant_id,site_id,type)
);
CREATE INDEX connector_tenant_site_idx ON connector(tenant_id,site_id,status);

CREATE TABLE connector_oauth_state (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL,
  connector_id uuid NOT NULL,
  state_hash text NOT NULL UNIQUE CHECK(length(state_hash)=64),
  pkce_verifier_ref text,
  requested_scopes text[] NOT NULL,
  redirect_path text NOT NULL DEFAULT '/settings/connectors',
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT oauth_site_tenant_fk FOREIGN KEY(site_id,tenant_id) REFERENCES site(id,tenant_id),
  CONSTRAINT oauth_connector_tenant_fk FOREIGN KEY(connector_id,tenant_id)
    REFERENCES connector(id,tenant_id)
);
CREATE INDEX connector_oauth_pending_idx
  ON connector_oauth_state(tenant_id,connector_id,expires_at)
  WHERE consumed_at IS NULL;

CREATE TABLE connector_sync (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  connector_id uuid NOT NULL,
  kind text NOT NULL CHECK(kind IN('backfill','incremental')),
  idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 200),
  cursor_json jsonb NOT NULL DEFAULT '{}',
  range_start date NOT NULL,
  range_end date NOT NULL,
  status text NOT NULL DEFAULT 'queued'
    CHECK(status IN('queued','running','completed','partial','failed','cancelled')),
  counts_json jsonb NOT NULL DEFAULT '{"days_completed":0,"rows_seen":0,"rows_upserted":0}',
  requested_by uuid NOT NULL,
  lease_until timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT sync_connector_tenant_fk FOREIGN KEY(connector_id,tenant_id)
    REFERENCES connector(id,tenant_id),
  UNIQUE(id,tenant_id),
  UNIQUE(tenant_id,connector_id,idempotency_key),
  CHECK(range_start<=range_end)
);
CREATE INDEX connector_sync_claim_idx ON connector_sync(status,created_at)
  WHERE status IN('queued','running');
CREATE INDEX connector_sync_tenant_idx ON connector_sync(tenant_id,connector_id,created_at DESC);

CREATE TABLE search_metric (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL,
  page_id uuid,
  metric_date date NOT NULL,
  query_hash text NOT NULL CHECK(length(query_hash)=64),
  page_url text NOT NULL,
  page_url_hash text NOT NULL CHECK(length(page_url_hash)=64),
  country text NOT NULL DEFAULT '',
  device text NOT NULL DEFAULT '',
  search_type text NOT NULL DEFAULT 'web',
  clicks double precision NOT NULL CHECK(clicks>=0),
  impressions double precision NOT NULL CHECK(impressions>=0),
  ctr double precision NOT NULL CHECK(ctr>=0 AND ctr<=1),
  position double precision NOT NULL CHECK(position>=0),
  source_sync_id uuid NOT NULL,
  ingested_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT metric_site_tenant_fk FOREIGN KEY(site_id,tenant_id) REFERENCES site(id,tenant_id),
  CONSTRAINT metric_page_tenant_fk FOREIGN KEY(page_id,tenant_id) REFERENCES page(id,tenant_id),
  CONSTRAINT metric_sync_tenant_fk FOREIGN KEY(source_sync_id,tenant_id)
    REFERENCES connector_sync(id,tenant_id),
  UNIQUE(tenant_id,site_id,metric_date,query_hash,page_url_hash,country,device,search_type)
);
CREATE INDEX search_metric_site_date_idx ON search_metric(tenant_id,site_id,metric_date DESC);
CREATE INDEX search_metric_page_date_idx ON search_metric(tenant_id,page_id,metric_date DESC)
  WHERE page_id IS NOT NULL;

ALTER TABLE connector ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_oauth_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_sync ENABLE ROW LEVEL SECURITY;
ALTER TABLE search_metric ENABLE ROW LEVEL SECURITY;

CREATE POLICY connector_tenant_isolation ON connector
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY connector_oauth_state_tenant_isolation ON connector_oauth_state
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY connector_sync_tenant_isolation ON connector_sync
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY search_metric_tenant_isolation ON search_metric
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
