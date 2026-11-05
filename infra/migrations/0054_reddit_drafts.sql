-- Reddit: draft a post or a reply, a person approves it, and the person posts
-- it on Reddit themselves.
--
-- Reddit stopped accepting new Data API access requests on 2026-10-31 and is
-- retiring the public API, so nothing here holds a Reddit credential or calls
-- Reddit. The product drafts; the hand-off opens Reddit with the text ready;
-- the person presses Post. Afterwards they may paste the link so the draft
-- records where it went.
--
-- The citation scan also keeps the Reddit threads an answer engine cited, so
-- the threads worth replying in come from evidence rather than guesswork.
--
-- Additive. Rollback: drop table reddit_draft; drop column thread_urls.
BEGIN;

ALTER TABLE ai_citation_observation
  ADD COLUMN thread_urls jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE reddit_draft (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  kind text NOT NULL CHECK(kind IN('post','reply')),
  subreddit text NOT NULL CHECK(subreddit ~ '^[A-Za-z0-9_]{2,21}$'),
  -- The thread being replied to; null for a new post.
  thread_url text CHECK(
    thread_url IS NULL
    OR thread_url ~ '^https://www\.reddit\.com/r/[A-Za-z0-9_]{2,21}/comments/[a-z0-9]{3,12}/'
  ),
  -- The question the thread answers, from the citation prompt or the person.
  question text NOT NULL CHECK(char_length(question) BETWEEN 1 AND 500),
  -- Text of the thread the person pasted, so the reply answers what was asked.
  context text NOT NULL DEFAULT '' CHECK(char_length(context)<=6000),
  page_url text CHECK(page_url IS NULL OR char_length(page_url)<=2000),
  title text NOT NULL DEFAULT '' CHECK(char_length(title)<=300),
  body text NOT NULL CHECK(char_length(body) BETWEEN 1 AND 10000),
  status text NOT NULL DEFAULT 'draft'
    CHECK(status IN('draft','approved','handed_off','posted','dismissed')),
  provider text NOT NULL CHECK(provider IN('anthropic','openai')),
  model text NOT NULL CHECK(char_length(model) BETWEEN 1 AND 120),
  cost_micros bigint NOT NULL DEFAULT 0 CHECK(cost_micros>=0),
  created_by uuid NOT NULL,
  approved_by uuid,
  approved_at timestamptz,
  handed_off_at timestamptz,
  posted_url text CHECK(
    posted_url IS NULL
    OR posted_url ~ '^https://www\.reddit\.com/r/[A-Za-z0-9_]{2,21}/comments/'
  ),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id,tenant_id),
  CHECK(kind<>'reply' OR thread_url IS NOT NULL),
  CHECK(kind<>'post' OR char_length(title)>=1),
  CHECK(status NOT IN('approved','handed_off','posted') OR approved_at IS NOT NULL)
);
CREATE INDEX reddit_draft_site_idx ON reddit_draft(tenant_id,site_id,created_at DESC);
CREATE INDEX reddit_draft_subreddit_idx ON reddit_draft(tenant_id,site_id,subreddit,handed_off_at DESC);

ALTER TABLE reddit_draft ENABLE ROW LEVEL SECURITY;
ALTER TABLE reddit_draft FORCE ROW LEVEL SECURITY;
CREATE POLICY reddit_draft_tenant_isolation ON reddit_draft
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
