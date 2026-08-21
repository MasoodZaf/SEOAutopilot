BEGIN;

ALTER TABLE page_observation ADD COLUMN canonical_url text;
ALTER TABLE page_observation ADD COLUMN robots_directives text[] NOT NULL DEFAULT '{}';
ALTER TABLE page_observation ADD COLUMN structured_data_json jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE link_edge (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  crawl_job_id uuid NOT NULL REFERENCES crawl_job(id),
  source_page_id uuid NOT NULL REFERENCES page(id),
  target_url text NOT NULL,
  target_url_hash text NOT NULL CHECK(length(target_url_hash)=64),
  anchor_text text NOT NULL DEFAULT '',
  rel_values text[] NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX link_edge_source_idx ON link_edge(tenant_id,crawl_job_id,source_page_id);
CREATE INDEX link_edge_target_idx ON link_edge(tenant_id,crawl_job_id,target_url_hash);

ALTER TABLE link_edge ENABLE ROW LEVEL SECURITY;
CREATE POLICY link_edge_tenant_isolation ON link_edge
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
