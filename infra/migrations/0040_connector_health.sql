BEGIN;

-- What a connection manager needs to say that the row did not already hold.
--
-- On 2026-09-18 every Google connector in production had been
-- `reauthorization_required` for three to five days. The hourly invariant check
-- had failed on it every hour, into a journal nobody reads, and the settings
-- page showed the status as one grey word among six. Nothing the tenant looks
-- at said when a connector was last known to work, or why it stopped.
--
-- `last_checked_at` is the last time a grant was proven good against the
-- provider -- by a sync renewing it, or by the worker's daily check doing so
-- on purpose. `last_error_code` is the reason the most recent attempt failed,
-- cleared by the next success. Both are written by the worker and the consent
-- callback; neither is ever a secret.
ALTER TABLE connector ADD COLUMN last_checked_at timestamptz;
ALTER TABLE connector ADD COLUMN last_error_code text
  CHECK(last_error_code IS NULL OR length(last_error_code) <= 80);

COMMIT;
