BEGIN;

-- A routine that keeps Search Console current.
--
-- Every other routine kind reads evidence that something else put there.
-- `keyword_refresh` clusters `search_query`, the weekly report reads
-- `search_metric` -- and nothing scheduled the sync that fills either. Search
-- Console data arrived only when somebody made an API call by hand, so
-- measurement was a one-off rather than a capability, and the routines built on
-- top of it were skipping with `no_search_query_evidence`.
ALTER TABLE routine DROP CONSTRAINT IF EXISTS routine_kind_check;
ALTER TABLE routine ADD CONSTRAINT routine_kind_check
  CHECK(kind IN(
    'site_audit','keyword_refresh','sitemap_coverage','content_briefs',
    'competitor_scan','ai_visibility_scan','weekly_report','search_console_sync'
  ));

COMMIT;
