BEGIN;

ALTER TABLE crawl_job ADD COLUMN attempts integer NOT NULL DEFAULT 0;
ALTER TABLE crawl_job ADD COLUMN last_heartbeat_at timestamptz;

CREATE TABLE page (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  normalized_url text NOT NULL,
  url_hash text NOT NULL CHECK(length(url_hash)=64),
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  lifecycle_status text NOT NULL DEFAULT 'active',
  UNIQUE(site_id,url_hash)
);
CREATE INDEX page_tenant_site_idx ON page(tenant_id,site_id,lifecycle_status);

CREATE TABLE page_observation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  page_id uuid NOT NULL REFERENCES page(id),
  crawl_job_id uuid NOT NULL REFERENCES crawl_job(id),
  observed_at timestamptz NOT NULL DEFAULT now(),
  http_status integer,
  final_url text NOT NULL,
  title text,
  meta_description text,
  h1_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  word_count integer NOT NULL DEFAULT 0,
  content_hash text,
  rendered boolean NOT NULL DEFAULT false
);
CREATE INDEX observation_tenant_crawl_idx ON page_observation(tenant_id,crawl_job_id,observed_at);

ALTER TABLE page ENABLE ROW LEVEL SECURITY;
ALTER TABLE page_observation ENABLE ROW LEVEL SECURITY;
CREATE POLICY page_tenant_isolation ON page
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY observation_tenant_isolation ON page_observation
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
