-- Live progress for a running crawl.
--
-- The crawler keeps its counters in memory and writes every page in one burst
-- at the end, so from the outside a 13-minute crawl was indistinguishable from
-- a stuck one. The heartbeat now writes a snapshot here every few seconds.
-- Additive with a default: old crawler builds simply never write it.
BEGIN;

ALTER TABLE crawl_job
  ADD COLUMN progress jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
