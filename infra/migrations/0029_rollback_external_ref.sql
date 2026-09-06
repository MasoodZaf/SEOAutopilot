BEGIN;

-- A rollback now has an address. Closing a pull request or opening a revert one
-- produces a URL, and without somewhere to record it the receipt says a
-- rollback happened while giving nobody a way to check. Defaulted to empty
-- rather than nullable so a receipt written by older code still reads the same.
ALTER TABLE rollback_receipt
  ADD COLUMN IF NOT EXISTS external_ref text NOT NULL DEFAULT '';

COMMIT;
