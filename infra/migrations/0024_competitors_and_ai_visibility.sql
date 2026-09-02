BEGIN;

-- Competitors are named explicitly by a human. There is no discovery: the scan
-- fetches exactly the URLs on record, so the crawler's same-host containment
-- invariant is never widened and no third-party site is traversed.
CREATE TABLE competitor (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  normalized_host text NOT NULL CHECK(char_length(normalized_host) BETWEEN 3 AND 253),
  label text NOT NULL CHECK(char_length(label) BETWEEN 1 AND 120),
  status text NOT NULL DEFAULT 'active' CHECK(status IN('active','paused')),
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,site_id,normalized_host),
  UNIQUE(id,tenant_id)
);
CREATE INDEX competitor_tenant_site_idx ON competitor(tenant_id,site_id,status);

CREATE TABLE competitor_page (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  competitor_id uuid NOT NULL,
  site_id uuid NOT NULL REFERENCES site(id),
  normalized_url text NOT NULL CHECK(char_length(normalized_url) BETWEEN 8 AND 8192),
  url_hash text NOT NULL CHECK(length(url_hash)=64),
  -- Optional cluster this page is tracked against, so a comparison is anchored
  -- to a topic rather than to the whole site.
  keyword_cluster_key text CHECK(char_length(keyword_cluster_key)<=200),
  status text NOT NULL DEFAULT 'active' CHECK(status IN('active','paused')),
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,competitor_id,url_hash),
  UNIQUE(id,tenant_id),
  CONSTRAINT competitor_page_competitor_tenant_fk FOREIGN KEY(competitor_id,tenant_id)
    REFERENCES competitor(id,tenant_id) ON DELETE CASCADE
);
CREATE INDEX competitor_page_tenant_site_idx ON competitor_page(tenant_id,site_id,status);

CREATE TABLE competitor_scan (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  routine_run_id uuid,
  status text NOT NULL DEFAULT 'running'
    CHECK(status IN('running','completed','partial','failed')),
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  pages_requested integer NOT NULL DEFAULT 0 CHECK(pages_requested>=0),
  pages_observed integer NOT NULL DEFAULT 0 CHECK(pages_observed>=0),
  pages_blocked integer NOT NULL DEFAULT 0 CHECK(pages_blocked>=0),
  pages_failed integer NOT NULL DEFAULT 0 CHECK(pages_failed>=0),
  error_code text CHECK(char_length(error_code)<=80),
  UNIQUE(id,tenant_id)
);
CREATE INDEX competitor_scan_tenant_site_idx
  ON competitor_scan(tenant_id,site_id,started_at DESC,id DESC);

-- Structural evidence only. Competitor body text is never stored; a content
-- hash records that the page changed without retaining what it said.
CREATE TABLE competitor_observation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  competitor_scan_id uuid NOT NULL,
  competitor_page_id uuid NOT NULL,
  site_id uuid NOT NULL,
  observed_at timestamptz NOT NULL DEFAULT now(),
  outcome text NOT NULL
    CHECK(outcome IN('observed','robots_disallowed','unreachable','not_html','too_large')),
  http_status integer CHECK(http_status BETWEEN 100 AND 599),
  title text CHECK(char_length(title)<=1000),
  meta_description text CHECK(char_length(meta_description)<=2000),
  h1_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  heading_count integer NOT NULL DEFAULT 0 CHECK(heading_count>=0),
  word_count integer NOT NULL DEFAULT 0 CHECK(word_count>=0),
  internal_link_count integer NOT NULL DEFAULT 0 CHECK(internal_link_count>=0),
  structured_data_types text[] NOT NULL DEFAULT '{}',
  content_hash text CHECK(length(content_hash)=64),
  CONSTRAINT competitor_observation_scan_tenant_fk FOREIGN KEY(competitor_scan_id,tenant_id)
    REFERENCES competitor_scan(id,tenant_id),
  CONSTRAINT competitor_observation_page_tenant_fk FOREIGN KEY(competitor_page_id,tenant_id)
    REFERENCES competitor_page(id,tenant_id) ON DELETE CASCADE,
  UNIQUE(competitor_scan_id,competitor_page_id)
);
CREATE INDEX competitor_observation_page_idx
  ON competitor_observation(tenant_id,competitor_page_id,observed_at DESC);

-- Answer-engine readiness measured from first-party evidence only. Citation
-- monitoring needs a certified provider and is not represented here, so this
-- never claims to describe what an answer engine actually said.
CREATE TABLE ai_visibility_snapshot (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  routine_run_id uuid,
  crawl_job_id uuid,
  captured_on date NOT NULL,
  readiness_score double precision NOT NULL
    CHECK(readiness_score>=0 AND readiness_score<=100),
  factors_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  content_hash text NOT NULL CHECK(length(content_hash)=64),
  citation_source text NOT NULL DEFAULT 'none'
    CHECK(citation_source IN('none')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,site_id,captured_on),
  UNIQUE(id,tenant_id)
);
CREATE INDEX ai_visibility_snapshot_tenant_site_idx
  ON ai_visibility_snapshot(tenant_id,site_id,captured_on DESC);

ALTER TABLE competitor ENABLE ROW LEVEL SECURITY;
ALTER TABLE competitor_page ENABLE ROW LEVEL SECURITY;
ALTER TABLE competitor_scan ENABLE ROW LEVEL SECURITY;
ALTER TABLE competitor_observation ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_visibility_snapshot ENABLE ROW LEVEL SECURITY;

CREATE POLICY competitor_tenant_isolation ON competitor
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY competitor_page_tenant_isolation ON competitor_page
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY competitor_scan_tenant_isolation ON competitor_scan
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY competitor_observation_tenant_isolation ON competitor_observation
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY ai_visibility_snapshot_tenant_isolation ON ai_visibility_snapshot
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
