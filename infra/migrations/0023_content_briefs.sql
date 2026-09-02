BEGIN;

-- A content brief is a governed advisory artifact derived from a keyword
-- cluster and the page evidence for its target. It is not a proposal: it
-- contains no diff, carries no deployment authority, and cannot reach an
-- adapter. Turning a brief into a change still goes through the proposal
-- lifecycle and its approval gates.
CREATE TABLE content_brief (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  keyword_cluster_id uuid NOT NULL,
  analysis_run_id uuid NOT NULL,
  routine_run_id uuid,
  -- A refresh targets an existing page; a new_page brief has no target yet.
  kind text NOT NULL CHECK(kind IN('refresh','new_page')),
  status text NOT NULL DEFAULT 'queued'
    CHECK(status IN('queued','in_progress','done','dismissed')),
  target_page_id uuid,
  cluster_label text NOT NULL CHECK(char_length(cluster_label) BETWEEN 1 AND 200),
  intent text NOT NULL CHECK(intent IN(
    'informational','commercial','transactional','navigational'
  )),
  answer_engine_candidate boolean NOT NULL DEFAULT false,
  priority_score double precision NOT NULL
    CHECK(priority_score>=0 AND priority_score<=100),
  -- Sections and evidence reference stored observations and query hashes only.
  -- No readable query term is ever persisted here.
  sections_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  query_hashes text[] NOT NULL DEFAULT '{}',
  content_hash text NOT NULL CHECK(length(content_hash)=64),
  dismissed_reason text CHECK(char_length(dismissed_reason)<=200),
  dismissed_by uuid,
  dismissed_at timestamptz,
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT content_brief_cluster_tenant_fk FOREIGN KEY(keyword_cluster_id,tenant_id)
    REFERENCES keyword_cluster(id,tenant_id) ON DELETE CASCADE,
  CONSTRAINT content_brief_run_tenant_fk FOREIGN KEY(analysis_run_id,tenant_id)
    REFERENCES keyword_analysis_run(id,tenant_id),
  CONSTRAINT content_brief_page_tenant_fk FOREIGN KEY(target_page_id,tenant_id)
    REFERENCES page(id,tenant_id),
  CONSTRAINT content_brief_target_check
    CHECK((kind='refresh' AND target_page_id IS NOT NULL)
       OR (kind='new_page' AND target_page_id IS NULL)),
  CONSTRAINT content_brief_dismissal_check
    CHECK((status='dismissed') = (dismissed_reason IS NOT NULL)),
  UNIQUE(tenant_id,keyword_cluster_id),
  UNIQUE(id,tenant_id)
);
CREATE INDEX content_brief_queue_idx
  ON content_brief(tenant_id,site_id,status,priority_score DESC,id);
CREATE INDEX content_brief_run_idx ON content_brief(tenant_id,analysis_run_id);

ALTER TABLE content_brief ENABLE ROW LEVEL SECURITY;
CREATE POLICY content_brief_tenant_isolation ON content_brief
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

ALTER TABLE routine DROP CONSTRAINT routine_kind_check;
ALTER TABLE routine ADD CONSTRAINT routine_kind_check
  CHECK(kind IN(
    'site_audit','keyword_refresh','sitemap_coverage','content_briefs',
    'competitor_scan','ai_visibility_scan','weekly_report'
  ));

COMMIT;
