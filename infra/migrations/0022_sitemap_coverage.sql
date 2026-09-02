BEGIN;

-- What each crawl found when it read the site's sitemaps. Recorded per crawl so
-- coverage can be compared across runs rather than only described for "now".
CREATE TABLE sitemap_source (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  crawl_job_id uuid NOT NULL REFERENCES crawl_job(id),
  sitemap_url text NOT NULL CHECK(char_length(sitemap_url) BETWEEN 8 AND 8192),
  discovered_via text NOT NULL
    CHECK(discovered_via IN('well_known','robots_txt')),
  status text NOT NULL
    CHECK(status IN('fetched','unreachable','malformed','out_of_scope')),
  declared_url_count integer NOT NULL DEFAULT 0 CHECK(declared_url_count>=0),
  in_scope_url_count integer NOT NULL DEFAULT 0 CHECK(in_scope_url_count>=0),
  truncated boolean NOT NULL DEFAULT false,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,crawl_job_id,sitemap_url),
  UNIQUE(id,tenant_id)
);
CREATE INDEX sitemap_source_tenant_site_idx
  ON sitemap_source(tenant_id,site_id,fetched_at DESC,id DESC);

-- Every in-scope URL a sitemap declared, whether or not the crawl reached it.
-- This is what makes "declared but never crawled" answerable.
CREATE TABLE sitemap_url (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  crawl_job_id uuid NOT NULL REFERENCES crawl_job(id),
  url_hash text NOT NULL CHECK(length(url_hash)=64),
  site_id uuid NOT NULL,
  sitemap_source_id uuid NOT NULL,
  normalized_url text NOT NULL CHECK(char_length(normalized_url) BETWEEN 1 AND 8192),
  PRIMARY KEY(tenant_id,crawl_job_id,url_hash),
  CONSTRAINT sitemap_url_source_tenant_fk FOREIGN KEY(sitemap_source_id,tenant_id)
    REFERENCES sitemap_source(id,tenant_id)
);
CREATE INDEX sitemap_url_crawl_idx ON sitemap_url(tenant_id,crawl_job_id,normalized_url);

-- Sitemap coverage is published through the same report table as the weekly
-- digest, so one reader and one delivery path cover both.
ALTER TABLE report DROP CONSTRAINT report_kind_check;
ALTER TABLE report ADD CONSTRAINT report_kind_check
  CHECK(kind IN(
    'weekly_digest','audit_summary','competitor_digest',
    'ai_visibility_digest','sitemap_coverage'
  ));

ALTER TABLE sitemap_source ENABLE ROW LEVEL SECURITY;
ALTER TABLE sitemap_url ENABLE ROW LEVEL SECURITY;
CREATE POLICY sitemap_source_tenant_isolation ON sitemap_source
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY sitemap_url_tenant_isolation ON sitemap_url
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
