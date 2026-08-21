BEGIN;

CREATE TABLE site_verification_challenge (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  method text NOT NULL DEFAULT 'dns_txt' CHECK (method IN ('dns_txt')),
  token_hash text NOT NULL CHECK (length(token_hash) = 64),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','verified','expired','revoked')),
  expires_at timestamptz NOT NULL,
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  verified_at timestamptz
);
CREATE INDEX verification_tenant_site_idx ON site_verification_challenge(tenant_id,site_id,status);

CREATE TABLE crawl_job (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  kind text NOT NULL DEFAULT 'full' CHECK (kind IN ('full','incremental')),
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','partial','failed','cancelled')),
  requested_by uuid NOT NULL,
  config_snapshot jsonb NOT NULL,
  lease_until timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX crawl_job_tenant_site_idx ON crawl_job(tenant_id,site_id,status);

ALTER TABLE site_verification_challenge ENABLE ROW LEVEL SECURITY;
ALTER TABLE crawl_job ENABLE ROW LEVEL SECURITY;
CREATE POLICY verification_tenant_isolation ON site_verification_challenge
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY crawl_job_tenant_isolation ON crawl_job
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
