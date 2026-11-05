-- A workspace with one person who can approve could never ship anything:
-- every proposal is theirs, and an author may not approve their own. The owner
-- of such a workspace may now self-review a low- or medium-risk proposal after
-- confirming they read the diff. The approval row records that it was a
-- self-review, so the audit trail never presents it as a second pair of eyes.
-- High-risk, template-wide and prohibited changes still need another person.
--
-- Additive, defaulted false; existing approvals were all by a non-author.
-- Rollback: drop the column (self-reviewed rows then read as ordinary approvals,
-- so revoke them first if rolling back for policy reasons).
BEGIN;

ALTER TABLE proposal_approval ADD COLUMN self_reviewed boolean NOT NULL DEFAULT false;

COMMIT;
