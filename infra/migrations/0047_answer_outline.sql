-- How each page is laid out for an answer engine to quote it.
--
-- Answer engines lift a passage, and the one they can use sits directly under
-- the question it answers. The crawler now records, per page, its H2/H3
-- headings, whether each reads as a question, and the size and shape of the
-- first block of copy under it, plus how many FAQPage/QAPage questions the
-- page declares and shows. Heading text is kept, as H1s already are; body copy
-- is not.
--
-- Additive and nullable: old crawler builds never write it, and the rules that
-- read it stay silent on observations without it.
-- Rollback: drop the column after reverting the crawler and worker.
BEGIN;

ALTER TABLE page_observation
  ADD COLUMN answer_outline_json jsonb;

COMMIT;
