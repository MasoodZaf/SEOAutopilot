-- AI-drafted blog posts, reviewed by a person before they become a proposal.
--
-- A content draft is written by a model from an accepted new-page brief. It is
-- never deployable on its own: a person edits it, resolves every flagged
-- claim, and submits it, which creates an ordinary proposal that still needs
-- its approvals and a merged pull request before anything reaches a site.
--
-- The model's own output is kept unchanged in `generated_json` so a reviewer
-- (and an auditor later) can see exactly what a person changed.
--
-- Additive, with two widened constraints:
--   * tenant_credential.provider accepts 'anthropic_api_key' and
--     'openai_api_key'. Drafting is bring-your-own-key only: a workspace's
--     posts are written with, and billed to, a key that workspace stored.
--     The deployment's own keys are never used for drafting.
--   * proposal.opportunity_id becomes nullable: a new post starts from a
--     brief, not from a finding. A proposal must still name one or the other.
-- Rollback: drop content_draft and proposal.content_draft_id; restoring NOT
-- NULL on opportunity_id requires deleting draft-born proposals first.
BEGIN;

ALTER TABLE tenant_credential DROP CONSTRAINT tenant_credential_provider_check;
ALTER TABLE tenant_credential ADD CONSTRAINT tenant_credential_provider_check
  CHECK(provider IN('google_oauth_client','github_app','anthropic_api_key','openai_api_key'));

CREATE TABLE content_draft (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  content_brief_id uuid NOT NULL,
  requested_by uuid NOT NULL,
  status text NOT NULL DEFAULT 'queued'
    CHECK(status IN('queued','running','ready','failed','submitted','withdrawn')),
  idempotency_key text NOT NULL CHECK(char_length(idempotency_key) BETWEEN 8 AND 128),
  request_hash text NOT NULL CHECK(length(request_hash)=64),
  -- Worker lease, swept by the reaper like every other leased job.
  lease_until timestamptz,
  attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
  finished_at timestamptz,
  error_code text CHECK(char_length(error_code)<=80),
  -- Which of the workspace's keys writes it, chosen when it is requested.
  provider text NOT NULL CHECK(provider IN('anthropic','openai')),
  -- What produced it, and what it cost (integer micro-dollars; an estimate
  -- from list prices, the provider's invoice is authoritative).
  model text CHECK(char_length(model)<=80),
  prompt_version text CHECK(char_length(prompt_version)<=40),
  input_hash text CHECK(length(input_hash)=64),
  input_tokens integer NOT NULL DEFAULT 0 CHECK(input_tokens>=0),
  output_tokens integer NOT NULL DEFAULT 0 CHECK(output_tokens>=0),
  cost_micros bigint NOT NULL DEFAULT 0 CHECK(cost_micros>=0),
  -- The model's output, verbatim. Never edited after it is written.
  generated_json jsonb,
  -- The reviewer's working copy.
  title text CHECK(char_length(title)<=200),
  slug text CHECK(slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug)<=80),
  meta_description text CHECK(char_length(meta_description)<=320),
  body_markdown text CHECK(char_length(body_markdown)<=60000),
  author_name text CHECK(char_length(author_name)<=120),
  -- Claims to verify and other review flags; each carries `resolved`.
  flags_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT content_draft_brief_tenant_fk FOREIGN KEY(content_brief_id,tenant_id)
    REFERENCES content_brief(id,tenant_id),
  UNIQUE(id,tenant_id),
  UNIQUE(tenant_id,idempotency_key)
);

-- One live draft per brief: a second request while one is queued, being
-- written or awaiting review is a duplicate, not a second post.
CREATE UNIQUE INDEX content_draft_one_live_per_brief_idx
  ON content_draft(tenant_id,content_brief_id)
  WHERE status IN('queued','running','ready');
CREATE INDEX content_draft_tenant_site_idx
  ON content_draft(tenant_id,site_id,created_at DESC);
-- The monthly spend check reads cost by tenant and time.
CREATE INDEX content_draft_tenant_cost_idx
  ON content_draft(tenant_id,created_at) WHERE cost_micros>0;

ALTER TABLE content_draft ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_draft FORCE ROW LEVEL SECURITY;
CREATE POLICY content_draft_tenant_isolation ON content_draft
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

ALTER TABLE proposal ALTER COLUMN opportunity_id DROP NOT NULL;
ALTER TABLE proposal ADD COLUMN content_draft_id uuid;
ALTER TABLE proposal ADD CONSTRAINT proposal_content_draft_tenant_fk
  FOREIGN KEY(content_draft_id,tenant_id) REFERENCES content_draft(id,tenant_id);
ALTER TABLE proposal ADD CONSTRAINT proposal_origin_check
  CHECK(opportunity_id IS NOT NULL OR content_draft_id IS NOT NULL);
-- A draft becomes at most one live proposal.
CREATE UNIQUE INDEX proposal_one_live_per_draft_idx
  ON proposal(tenant_id,content_draft_id)
  WHERE content_draft_id IS NOT NULL
    AND status NOT IN('rejected','expired','failed');

COMMIT;
