BEGIN;

ALTER TABLE page_observation
  ADD COLUMN link_count_total integer NOT NULL DEFAULT 0,
  ADD COLUMN links_truncated boolean NOT NULL DEFAULT false;

COMMIT;
