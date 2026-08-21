BEGIN;

ALTER TABLE page_observation
  ADD CONSTRAINT page_observation_page_crawl_unique UNIQUE(page_id,crawl_job_id);
ALTER TABLE link_edge
  ADD CONSTRAINT link_edge_evidence_unique
  UNIQUE(crawl_job_id,source_page_id,target_url_hash,anchor_text,rel_values);

CREATE TABLE scoring_version (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code_version text NOT NULL UNIQUE,
  factor_config_json jsonb NOT NULL,
  active_from timestamptz NOT NULL DEFAULT now(),
  retired_at timestamptz
);
INSERT INTO scoring_version(id,code_version,factor_config_json)
VALUES(
  '019d0000-0000-7000-8000-000000000090',
  'technical-v1',
  '{"formula":"impact*confidence*urgency/(0.25+0.75*effort)*risk","risk":{"low":1.0,"medium":0.75,"high":0.35,"prohibited":0.0}}'::jsonb
);

CREATE TABLE analysis_run (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  crawl_job_id uuid NOT NULL REFERENCES crawl_job(id),
  agent_type text NOT NULL DEFAULT 'technical_rules',
  agent_version text NOT NULL,
  evidence_cutoff timestamptz,
  request_hash text NOT NULL CHECK(length(request_hash)=64),
  status text NOT NULL CHECK(status IN('running','completed','failed')),
  finding_count integer NOT NULL DEFAULT 0,
  score_count integer NOT NULL DEFAULT 0,
  opportunity_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE(crawl_job_id,agent_type,agent_version,request_hash)
);
CREATE INDEX analysis_run_tenant_site_idx ON analysis_run(tenant_id,site_id,created_at DESC);

CREATE TABLE finding (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  page_id uuid NOT NULL REFERENCES page(id),
  analysis_run_id uuid NOT NULL REFERENCES analysis_run(id),
  scoring_version_id uuid NOT NULL REFERENCES scoring_version(id),
  rule_key text NOT NULL,
  severity text NOT NULL CHECK(severity IN('critical','high','medium','low','info')),
  category text NOT NULL DEFAULT 'technical',
  evidence_refs jsonb NOT NULL,
  summary text NOT NULL,
  details_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  confidence double precision NOT NULL CHECK(confidence>=0 AND confidence<=1),
  status text NOT NULL DEFAULT 'open' CHECK(status IN('open','resolved','suppressed')),
  fingerprint text NOT NULL CHECK(length(fingerprint)=64),
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,fingerprint)
);
CREATE INDEX finding_tenant_site_status_idx ON finding(tenant_id,site_id,status,severity);

CREATE TABLE page_score (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  page_id uuid NOT NULL REFERENCES page(id),
  observation_id uuid NOT NULL REFERENCES page_observation(id),
  analysis_run_id uuid NOT NULL REFERENCES analysis_run(id),
  scoring_version_id uuid NOT NULL REFERENCES scoring_version(id),
  calculated_at timestamptz NOT NULL DEFAULT now(),
  score integer NOT NULL CHECK(score>=0 AND score<=100),
  factors_json jsonb NOT NULL,
  evidence_cutoff timestamptz NOT NULL,
  UNIQUE(page_id,scoring_version_id,observation_id)
);
CREATE INDEX page_score_tenant_page_idx ON page_score(tenant_id,page_id,calculated_at DESC);

CREATE TABLE opportunity (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  page_id uuid NOT NULL REFERENCES page(id),
  type text NOT NULL DEFAULT 'technical',
  title text NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK(status IN('open','shortlisted','proposing','proposed','accepted','dismissed','expired')),
  impact double precision NOT NULL CHECK(impact>=0 AND impact<=1),
  confidence double precision NOT NULL CHECK(confidence>=0 AND confidence<=1),
  urgency double precision NOT NULL CHECK(urgency>=0 AND urgency<=1),
  effort double precision NOT NULL CHECK(effort>=0 AND effort<=1),
  risk text NOT NULL CHECK(risk IN('low','medium','high','prohibited')),
  score double precision NOT NULL CHECK(score>=0 AND score<=100),
  scoring_version_id uuid NOT NULL REFERENCES scoring_version(id),
  evidence_refs jsonb NOT NULL,
  fingerprint text NOT NULL CHECK(length(fingerprint)=64),
  suppressed_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,fingerprint)
);
CREATE INDEX opportunity_top20_idx ON opportunity(tenant_id,site_id,status,score DESC,fingerprint);

CREATE TABLE opportunity_finding (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  opportunity_id uuid NOT NULL REFERENCES opportunity(id),
  finding_id uuid NOT NULL REFERENCES finding(id),
  PRIMARY KEY(tenant_id,opportunity_id,finding_id)
);

ALTER TABLE analysis_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE finding ENABLE ROW LEVEL SECURITY;
ALTER TABLE page_score ENABLE ROW LEVEL SECURITY;
ALTER TABLE opportunity ENABLE ROW LEVEL SECURITY;
ALTER TABLE opportunity_finding ENABLE ROW LEVEL SECURITY;

CREATE POLICY analysis_run_tenant_isolation ON analysis_run
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY finding_tenant_isolation ON finding
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY page_score_tenant_isolation ON page_score
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY opportunity_tenant_isolation ON opportunity
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY opportunity_finding_tenant_isolation ON opportunity_finding
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
