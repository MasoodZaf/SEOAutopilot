-- Observed AI answer citations: does Claude or ChatGPT cite this site when
-- asked the questions its audience asks?
--
-- A workspace tracks up to a handful of questions per site. A routine asks
-- each one through the provider's API with web search on, using the
-- workspace's own key (bring-your-own-key, as for blog drafting), and records
-- whether the site was cited or named. The site is never named in the prompt.
--
-- Answers vary between runs and the API is not the consumer product, so each
-- observation records its provider and model, and readers report "cited in
-- N of M answers", never a rank. Answer text is third-party model output:
-- only a bounded excerpt is kept, and it is never passed to a model or tool.
--
-- Additive: three tenant-scoped tables under RLS; routine.kind widened to
-- accept 'ai_citation_scan'; tenant_credential.provider widened to accept
-- 'perplexity_api_key', a third engine (bring-your-own-key, like the others).
-- Rollback: drop the three tables; delete ai_citation_scan routines and
-- perplexity_api_key credentials, and restore the previous
-- routine_kind_check and tenant_credential_provider_check.
BEGIN;

CREATE TABLE ai_citation_prompt (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  prompt text NOT NULL CHECK(char_length(prompt) BETWEEN 8 AND 300),
  source text NOT NULL CHECK(source IN('question_cluster','manual')),
  keyword_cluster_id uuid,
  active boolean NOT NULL DEFAULT true,
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id,tenant_id)
);
CREATE UNIQUE INDEX ai_citation_prompt_unique_text_idx
  ON ai_citation_prompt(tenant_id,site_id,lower(prompt));
CREATE INDEX ai_citation_prompt_site_idx ON ai_citation_prompt(tenant_id,site_id,active);

CREATE TABLE ai_citation_run (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  routine_run_id uuid,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  prompts_asked integer NOT NULL DEFAULT 0 CHECK(prompts_asked>=0),
  answers integer NOT NULL DEFAULT 0 CHECK(answers>=0),
  cited integer NOT NULL DEFAULT 0 CHECK(cited>=0),
  mentioned integer NOT NULL DEFAULT 0 CHECK(mentioned>=0),
  cost_micros bigint NOT NULL DEFAULT 0 CHECK(cost_micros>=0),
  summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(id,tenant_id)
);
CREATE INDEX ai_citation_run_site_idx ON ai_citation_run(tenant_id,site_id,started_at DESC);

CREATE TABLE ai_citation_observation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  run_id uuid NOT NULL,
  prompt_id uuid NOT NULL,
  prompt text NOT NULL,
  provider text NOT NULL CHECK(provider IN('anthropic','openai','perplexity')),
  model text NOT NULL CHECK(char_length(model) BETWEEN 1 AND 120),
  status text NOT NULL CHECK(status IN('answered','failed')),
  error_code text CHECK(error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,80}$'),
  site_cited boolean NOT NULL DEFAULT false,
  site_mentioned boolean NOT NULL DEFAULT false,
  -- 1-based position of the site's first citation among distinct cited hosts.
  own_citation_rank integer CHECK(own_citation_rank IS NULL OR own_citation_rank>=1),
  cited_hosts jsonb NOT NULL DEFAULT '[]'::jsonb,
  own_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
  competitor_hosts jsonb NOT NULL DEFAULT '[]'::jsonb,
  answer_excerpt text CHECK(answer_excerpt IS NULL OR char_length(answer_excerpt)<=600),
  web_searches integer NOT NULL DEFAULT 0 CHECK(web_searches>=0),
  cost_micros bigint NOT NULL DEFAULT 0 CHECK(cost_micros>=0),
  observed_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(run_id,tenant_id) REFERENCES ai_citation_run(id,tenant_id) ON DELETE CASCADE,
  FOREIGN KEY(prompt_id,tenant_id) REFERENCES ai_citation_prompt(id,tenant_id) ON DELETE CASCADE,
  UNIQUE(run_id,prompt_id,provider)
);
CREATE INDEX ai_citation_observation_site_idx
  ON ai_citation_observation(tenant_id,site_id,observed_at DESC);

ALTER TABLE ai_citation_prompt ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_citation_prompt FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_citation_prompt_tenant_isolation ON ai_citation_prompt
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
ALTER TABLE ai_citation_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_citation_run FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_citation_run_tenant_isolation ON ai_citation_run
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
ALTER TABLE ai_citation_observation ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_citation_observation FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_citation_observation_tenant_isolation ON ai_citation_observation
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

ALTER TABLE tenant_credential DROP CONSTRAINT tenant_credential_provider_check;
ALTER TABLE tenant_credential ADD CONSTRAINT tenant_credential_provider_check
  CHECK(provider IN(
    'google_oauth_client','github_app','anthropic_api_key','openai_api_key',
    'perplexity_api_key'
  ));

ALTER TABLE routine DROP CONSTRAINT IF EXISTS routine_kind_check;
ALTER TABLE routine ADD CONSTRAINT routine_kind_check
  CHECK(kind IN(
    'site_audit','keyword_refresh','sitemap_coverage','content_briefs',
    'competitor_scan','ai_visibility_scan','weekly_report','search_console_sync',
    'analytics_sync','ai_citation_scan'
  ));

COMMIT;
