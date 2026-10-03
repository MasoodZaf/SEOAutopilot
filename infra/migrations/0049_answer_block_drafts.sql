-- AI-drafted direct answers for an existing page's question headings.
--
-- A content draft was always a new blog post written from a brief. It can now
-- also be an answer block: for a page whose question headings have no answer
-- an AI engine could quote (geo.question_without_direct_answer), a model
-- writes a short answer under each, a person edits them and resolves every
-- flagged claim, and submitting it proposes an edit to the page's own source
-- file -- the answers inserted under their headings, plus FAQPage JSON-LD
-- built from them in code. That proposal is high risk and goes through the
-- same approvals and merged pull request as any other change.
--
-- The questions are chosen by the server from the page's latest crawl, never
-- by the client, and stored on the draft as `answer_items_json`
-- ([{heading, answer}], answer null until the model writes it).
--
-- Additive, with one relaxed constraint:
--   * content_draft.kind, 'blog_post' for every existing row.
--   * content_brief_id becomes nullable; a CHECK keeps a blog post tied to
--     its brief and an answer block tied to its page.
--   * At most one live answer draft per page.
-- Rollback: delete answer_block drafts (and proposals that name them), drop
-- the index, the check and the three columns, and restore NOT NULL on
-- content_brief_id.
BEGIN;

ALTER TABLE content_draft
  ADD COLUMN kind text NOT NULL DEFAULT 'blog_post'
    CHECK(kind IN('blog_post','answer_block')),
  ADD COLUMN page_id uuid,
  ADD COLUMN answer_items_json jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE content_draft ALTER COLUMN content_brief_id DROP NOT NULL;

ALTER TABLE content_draft ADD CONSTRAINT content_draft_page_tenant_fk
  FOREIGN KEY(page_id,tenant_id) REFERENCES page(id,tenant_id);

ALTER TABLE content_draft ADD CONSTRAINT content_draft_kind_origin_check
  CHECK(
    (kind='blog_post' AND content_brief_id IS NOT NULL)
    OR (kind='answer_block' AND page_id IS NOT NULL)
  );

CREATE UNIQUE INDEX content_draft_one_live_answer_per_page_idx
  ON content_draft(tenant_id,page_id)
  WHERE kind='answer_block' AND status IN('queued','running','ready');

COMMIT;
