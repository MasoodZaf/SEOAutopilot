BEGIN;

-- Migration 0013 added suppressed_at/suppressed_by and the service has always
-- written status='suppressed', but the original CHECK from 0005 never listed
-- that value, so every suppression failed at the database. The read path
-- already treats 'suppressed' as a first-class status.
ALTER TABLE opportunity DROP CONSTRAINT opportunity_status_check;
ALTER TABLE opportunity ADD CONSTRAINT opportunity_status_check
  CHECK(status IN(
    'open','shortlisted','proposing','proposed','accepted',
    'dismissed','expired','suppressed'
  ));

COMMIT;
