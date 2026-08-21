BEGIN;

CREATE TABLE performance_run (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  page_id uuid NOT NULL REFERENCES page(id),
  crawl_job_id uuid NOT NULL REFERENCES crawl_job(id),
  status text NOT NULL DEFAULT 'queued' CHECK(status IN('queued','running','completed','failed')),
  strategy text NOT NULL DEFAULT 'mobile' CHECK(strategy IN('mobile','desktop')),
  source text NOT NULL DEFAULT 'pagespeed_insights' CHECK(source IN('pagespeed_insights')),
  target_url text NOT NULL CHECK(char_length(target_url) BETWEEN 8 AND 8192),
  idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 200),
  request_hash text NOT NULL CHECK(length(request_hash)=64),
  requested_by uuid NOT NULL,
  attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
  lease_until timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  error_code text CHECK(char_length(error_code)<=80),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,site_id,idempotency_key),
  UNIQUE(id,tenant_id)
);

CREATE UNIQUE INDEX performance_run_one_active_idx
  ON performance_run(tenant_id,site_id) WHERE status IN('queued','running');
CREATE INDEX performance_run_tenant_site_idx
  ON performance_run(tenant_id,site_id,created_at DESC,id DESC);

CREATE TABLE performance_observation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  performance_run_id uuid NOT NULL,
  site_id uuid NOT NULL REFERENCES site(id),
  page_id uuid NOT NULL REFERENCES page(id),
  observed_at timestamptz NOT NULL DEFAULT now(),
  strategy text NOT NULL CHECK(strategy IN('mobile','desktop')),
  source text NOT NULL CHECK(source IN('pagespeed_insights')),
  lighthouse_version text NOT NULL CHECK(char_length(lighthouse_version)<=80),
  performance_score integer NOT NULL CHECK(performance_score BETWEEN 0 AND 100),
  lcp_ms double precision CHECK(lcp_ms>=0),
  inp_ms double precision CHECK(inp_ms>=0),
  cls double precision CHECK(cls>=0),
  ttfb_ms double precision CHECK(ttfb_ms>=0),
  CONSTRAINT performance_observation_run_tenant_fk
    FOREIGN KEY(performance_run_id,tenant_id) REFERENCES performance_run(id,tenant_id),
  UNIQUE(performance_run_id),
  UNIQUE(id,tenant_id)
);
CREATE INDEX performance_observation_tenant_site_idx
  ON performance_observation(tenant_id,site_id,observed_at DESC,id DESC);

ALTER TABLE performance_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE performance_observation ENABLE ROW LEVEL SECURITY;
CREATE POLICY performance_run_tenant_isolation ON performance_run
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY performance_observation_tenant_isolation ON performance_observation
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
