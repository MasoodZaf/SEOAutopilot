BEGIN;

-- Make it visible whether anything has looked at a pending rollback.
--
-- Rolling back a *merged* deployment opens a revert pull request and stops.
-- The deployed content stays live until a person merges it, which is why
-- migration 0033 gave that state its own name rather than calling it
-- `rolled_back`. What 0033 could not do is find out what happened next:
-- nothing watched whether the revert was merged, closed or abandoned, so a
-- rollback stayed `rollback_pending` for ever. The pilot's emi-calculator sits
-- in exactly that state -- revert closed unmerged on 2026-09-06, change still
-- live, receipt still waiting.
--
-- These two columns are what makes the reconciler's work observable. Without
-- them "nothing has checked" and "checked, still open" look identical, which is
-- the same failure at one remove: a state that cannot be distinguished from an
-- absence of one.
ALTER TABLE rollback_receipt ADD COLUMN reconciled_at timestamptz;
ALTER TABLE rollback_receipt ADD COLUMN reconcile_error text;

-- Only pending rollbacks are ever swept, and there are few of them.
CREATE INDEX rollback_receipt_pending_idx
  ON rollback_receipt(reconciled_at NULLS FIRST)
  WHERE status = 'pending';

COMMIT;
