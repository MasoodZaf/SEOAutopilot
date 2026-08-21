BEGIN;

ALTER TABLE crawl_job
  ADD COLUMN result_summary jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
