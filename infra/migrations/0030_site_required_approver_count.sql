BEGIN;

-- How many approvers a change to this site needs, when the tenant wants
-- something other than the risk tier's default. Null means "use the tier",
-- which is what every existing site keeps, so this changes no behaviour until
-- somebody sets it deliberately.
--
-- Bounded at 5 for the same reason daily_change_budget is: a number nobody can
-- satisfy is a broken site, not a strict one. The lower bound is 1 because a
-- change no one has to approve is a different feature (Autopilot) with its own
-- controls, and must not be reachable by setting this to zero.
ALTER TABLE site
  ADD COLUMN IF NOT EXISTS required_approver_count integer;

ALTER TABLE site
  DROP CONSTRAINT IF EXISTS site_required_approver_count_check;

ALTER TABLE site
  ADD CONSTRAINT site_required_approver_count_check
  CHECK (required_approver_count IS NULL
         OR (required_approver_count >= 1 AND required_approver_count <= 5));

COMMIT;
