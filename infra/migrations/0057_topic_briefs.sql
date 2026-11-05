-- New-post briefs from a topic a person types, not only from search data.
--
-- A site too young to show up for anything it has no page for never gets a
-- new_page brief from keyword analysis, so it could never get a post. A topic
-- brief is a new_page brief with no keyword cluster behind it: the person who
-- asked for it is recorded instead, and the draft is written without search
-- terms. Keyword reruns upsert on the cluster, so they never touch these.
--
-- Rollback: delete source='topic' briefs (and their drafts, which reference
-- them), then restore NOT NULL on keyword_cluster_id and analysis_run_id and
-- drop the added columns, constraint and index.
BEGIN;

ALTER TABLE content_brief
  ALTER COLUMN keyword_cluster_id DROP NOT NULL,
  ALTER COLUMN analysis_run_id DROP NOT NULL,
  ADD COLUMN source text NOT NULL DEFAULT 'analysis' CHECK(source IN('analysis','topic')),
  ADD COLUMN requested_by uuid;

ALTER TABLE content_brief ADD CONSTRAINT content_brief_origin_check CHECK(
  (source='analysis' AND keyword_cluster_id IS NOT NULL AND analysis_run_id IS NOT NULL)
  OR (source='topic' AND kind='new_page' AND keyword_cluster_id IS NULL
      AND analysis_run_id IS NULL AND requested_by IS NOT NULL)
);

-- One live brief per topic per site; a dismissed one may be asked for again.
CREATE UNIQUE INDEX content_brief_topic_live_idx
  ON content_brief(tenant_id,site_id,lower(cluster_label))
  WHERE source='topic' AND status<>'dismissed';

COMMIT;
