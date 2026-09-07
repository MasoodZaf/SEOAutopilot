BEGIN;

-- What happens after the click.
--
-- Search Console says what a page ranks for and where it sits. It says nothing
-- about whether anyone who arrived did anything, so the product could move a
-- page up and never know whether that was worth doing. Migration 0034 added the
-- GA4 connector; it authorises and stores a credential, and nothing has ever
-- read GA4 data with it. This is the table that data lands in.
--
-- The grain is one row per (day, landing page, channel, device). GA4 reports a
-- landing page as a path plus its query string, and that string is stored
-- exactly as reported rather than normalised away: two rows differing only by
-- `?utm_source=` are two different reported rows, and collapsing them into one
-- key would make the second silently overwrite the first and lose its sessions.
--
-- `page_id` is resolved separately, by normalising the path against the site's
-- origin, so several reported rows can point at one crawled page without any of
-- them being merged.
CREATE TABLE analytics_metric (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL,
  page_id uuid,
  metric_date date NOT NULL,
  -- As GA4 reported it. May be "(other)" when a property exceeds its
  -- cardinality limit, which is a real bucket of sessions and not a path.
  landing_page text NOT NULL CHECK(char_length(landing_page) BETWEEN 1 AND 2048),
  landing_page_hash text NOT NULL CHECK(length(landing_page_hash)=64),
  channel_group text NOT NULL DEFAULT '',
  device text NOT NULL DEFAULT '',
  sessions double precision NOT NULL CHECK(sessions>=0),
  engaged_sessions double precision NOT NULL CHECK(engaged_sessions>=0),
  users double precision NOT NULL CHECK(users>=0),
  views double precision NOT NULL CHECK(views>=0),
  engagement_duration_seconds double precision NOT NULL CHECK(engagement_duration_seconds>=0),
  key_events double precision NOT NULL CHECK(key_events>=0),
  source_sync_id uuid NOT NULL,
  ingested_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT analytics_site_tenant_fk FOREIGN KEY(site_id,tenant_id) REFERENCES site(id,tenant_id),
  CONSTRAINT analytics_page_tenant_fk FOREIGN KEY(page_id,tenant_id) REFERENCES page(id,tenant_id),
  CONSTRAINT analytics_sync_tenant_fk FOREIGN KEY(source_sync_id,tenant_id)
    REFERENCES connector_sync(id,tenant_id),
  UNIQUE(tenant_id,site_id,metric_date,landing_page_hash,channel_group,device)
);
CREATE INDEX analytics_metric_site_date_idx
  ON analytics_metric(tenant_id,site_id,metric_date DESC);
CREATE INDEX analytics_metric_page_date_idx
  ON analytics_metric(tenant_id,page_id,metric_date DESC)
  WHERE page_id IS NOT NULL;

-- ENABLE and FORCE, for the reason migration 0027 records: the services own
-- this table, and an owner bypasses its own policies unless they are forced.
ALTER TABLE analytics_metric ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics_metric FORCE ROW LEVEL SECURITY;
CREATE POLICY analytics_metric_tenant_isolation ON analytics_metric
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

-- The routine that keeps it current, alongside the Search Console one. Both
-- queue a sync and let the worker that owns the credential do the paging.
ALTER TABLE routine DROP CONSTRAINT IF EXISTS routine_kind_check;
ALTER TABLE routine ADD CONSTRAINT routine_kind_check
  CHECK(kind IN(
    'site_audit','keyword_refresh','sitemap_coverage','content_briefs',
    'competitor_scan','ai_visibility_scan','weekly_report','search_console_sync',
    'analytics_sync'
  ));

COMMIT;
