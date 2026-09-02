BEGIN;

-- Actor IDs currently come from the authenticated server-side TenantContext. A durable
-- tenant membership table is not implemented yet, so actor columns intentionally remain
-- UUID audit references rather than dangling foreign keys.
ALTER TABLE opportunity
  ADD COLUMN IF NOT EXISTS suppressed_at timestamptz,
  ADD COLUMN IF NOT EXISTS suppressed_by uuid;

COMMIT;
