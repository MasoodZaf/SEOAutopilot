-- A tracked AI-citation question can come from Search Console.
--
-- Suggestions now include question-shaped queries the site already earns
-- impressions for, read from the sealed search terms by roles allowed to read
-- them. When one is tracked, its provenance is recorded as 'search_console'
-- (verified against the site's own query hash, never taken from the client).
--
-- Widened constraint only. Rollback: set source='manual' where it is
-- 'search_console', then restore the two-value check.
BEGIN;

ALTER TABLE ai_citation_prompt DROP CONSTRAINT ai_citation_prompt_source_check;
ALTER TABLE ai_citation_prompt ADD CONSTRAINT ai_citation_prompt_source_check
  CHECK(source IN('question_cluster','manual','search_console'));

COMMIT;
