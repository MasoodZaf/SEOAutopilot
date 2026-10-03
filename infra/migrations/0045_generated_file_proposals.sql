-- Proposals for a whole file the product generates from crawl evidence, such
-- as llms.txt, which start from neither a finding nor a content draft.
--
-- Additive, with one widened constraint:
--   * proposal.generator names the deterministic generator that wrote the
--     file ('llms_txt'); NULL for every existing proposal.
--   * proposal_origin_check accepts a generator as the third origin. A
--     proposal must still name exactly where it came from.
--   * At most one live proposal per site per generator, so two requests
--     racing each other cannot both open a pull request.
-- Rollback: delete generator-born proposals (and their approvals and
-- deployments), restore the two-origin check, drop the index and column.
BEGIN;
ALTER TABLE proposal ADD COLUMN generator text
  CHECK(generator IS NULL OR generator IN('llms_txt'));
ALTER TABLE proposal DROP CONSTRAINT proposal_origin_check;
ALTER TABLE proposal ADD CONSTRAINT proposal_origin_check
  CHECK(opportunity_id IS NOT NULL OR content_draft_id IS NOT NULL OR generator IS NOT NULL);
CREATE UNIQUE INDEX proposal_one_live_per_generator_idx
  ON proposal(tenant_id,site_id,generator)
  WHERE generator IS NOT NULL
    AND status NOT IN('rejected','expired','failed');
COMMIT;
