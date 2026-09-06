-- A rollback that only opened a pull request is not a rollback yet.
--
-- The GitHub adapter returned `applied` for both of its outcomes, so the
-- service wrote `rolled_back` on the deployment receipt and `failed` on the
-- proposal in both cases. When the deployed change had already been merged,
-- the adapter's actual effect was to open a revert pull request and say so --
-- `detail = 'revert_pull_request_opened_not_merged'`, and the pull request
-- body reads "This is not merged." -- while every structured field a report or
-- a query reads asserted the change was undone.
--
-- On the pilot site that revert was closed unmerged on 2026-09-06. The page
-- still carries the deployed change, and the receipt still said `rolled_back`.
--
-- These two states are the difference between "undone" and "an undo is
-- waiting for a human", and nothing above the database can be trusted to keep
-- them apart if the column cannot hold both.

ALTER TABLE deployment_receipt DROP CONSTRAINT deployment_receipt_status_check;
ALTER TABLE deployment_receipt ADD CONSTRAINT deployment_receipt_status_check
  CHECK(status IN('pending','applied','failed','rollback_pending','rolled_back'));

ALTER TABLE rollback_receipt DROP CONSTRAINT rollback_receipt_status_check;
ALTER TABLE rollback_receipt ADD CONSTRAINT rollback_receipt_status_check
  CHECK(status IN('pending','applied','failed'));

-- Correct the rows already written under the old behaviour. The adapter
-- recorded its own outcome in the receipt notes, so the rows that never
-- reversed anything can be identified rather than guessed at: a revert pull
-- request opened, or found already open, both left the change live.
--
-- This restores three facts that were overwritten together -- the deployment is
-- still in force, the proposal is still deployed, and the rollback is not
-- finished. It does not decide whether the revert was later merged, closed or
-- abandoned; nothing in this system knows that yet.

WITH unfinished AS (
  SELECT r.id, r.deployment_receipt_id, r.proposal_id, r.tenant_id
  FROM rollback_receipt r
  WHERE r.status = 'applied'
    AND (r.notes LIKE '%revert_pull_request_opened_not_merged'
         OR r.notes LIKE '%revert_pull_request_already_open')
)
UPDATE rollback_receipt r SET status = 'pending'
FROM unfinished u WHERE r.id = u.id;

UPDATE deployment_receipt d SET status = 'rollback_pending'
FROM rollback_receipt r
WHERE r.deployment_receipt_id = d.id
  AND r.tenant_id = d.tenant_id
  AND r.status = 'pending'
  AND d.status = 'rolled_back';

UPDATE proposal p SET status = 'deployed', updated_at = now()
FROM rollback_receipt r
WHERE r.proposal_id = p.id
  AND r.tenant_id = p.tenant_id
  AND r.status = 'pending'
  AND p.status = 'failed';
