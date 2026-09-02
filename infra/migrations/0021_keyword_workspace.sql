BEGIN;

-- The search query itself is user-typed data. The keyed HMAC stays the dedup
-- and join key; the readable term is held once per (site, query) in an
-- AES-256-GCM envelope and is decrypted only inside a tenant-scoped read path.
-- It is never returned by an aggregate endpoint and never logged.
CREATE TABLE search_query (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL,
  query_hash text NOT NULL CHECK(length(query_hash)=64),
  ciphertext bytea NOT NULL,
  nonce bytea NOT NULL CHECK(octet_length(nonce)=12),
  aad_hash text NOT NULL CHECK(length(aad_hash)=64),
  key_version text NOT NULL,
  -- Length and token count are non-reversible shape signals, safe to expose.
  term_length smallint NOT NULL CHECK(term_length BETWEEN 1 AND 400),
  token_count smallint NOT NULL CHECK(token_count BETWEEN 1 AND 60),
  is_question boolean NOT NULL DEFAULT false,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(tenant_id,site_id,query_hash),
  CONSTRAINT search_query_site_tenant_fk FOREIGN KEY(site_id,tenant_id)
    REFERENCES site(id,tenant_id)
);

-- A keyword analysis is versioned like scoring: the same evidence set and the
-- same algorithm version must reproduce the same clusters.
CREATE TABLE keyword_analysis_run (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  routine_run_id uuid,
  algorithm_version text NOT NULL CHECK(char_length(algorithm_version)<=40),
  status text NOT NULL DEFAULT 'completed'
    CHECK(status IN('completed','failed')),
  window_start date NOT NULL,
  window_end date NOT NULL,
  queries_considered integer NOT NULL DEFAULT 0 CHECK(queries_considered>=0),
  clusters_built integer NOT NULL DEFAULT 0 CHECK(clusters_built>=0),
  content_hash text NOT NULL CHECK(length(content_hash)=64),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT keyword_run_window_check CHECK(window_end >= window_start),
  UNIQUE(id,tenant_id),
  UNIQUE(tenant_id,site_id,window_start,window_end,algorithm_version)
);
CREATE INDEX keyword_analysis_run_tenant_site_idx
  ON keyword_analysis_run(tenant_id,site_id,created_at DESC,id DESC);

CREATE TABLE keyword_cluster (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  analysis_run_id uuid NOT NULL,
  -- Derived from the cluster's shared head tokens; readable and explainable.
  label text NOT NULL CHECK(char_length(label) BETWEEN 1 AND 200),
  cluster_key text NOT NULL CHECK(char_length(cluster_key) BETWEEN 1 AND 200),
  intent text NOT NULL CHECK(intent IN(
    'informational','commercial','transactional','navigational'
  )),
  answer_engine_candidate boolean NOT NULL DEFAULT false,
  member_count integer NOT NULL CHECK(member_count>0),
  clicks double precision NOT NULL DEFAULT 0 CHECK(clicks>=0),
  impressions double precision NOT NULL DEFAULT 0 CHECK(impressions>=0),
  ctr double precision NOT NULL DEFAULT 0 CHECK(ctr>=0 AND ctr<=1),
  best_position double precision CHECK(best_position>=0),
  average_position double precision CHECK(average_position>=0),
  striking_distance_count integer NOT NULL DEFAULT 0 CHECK(striking_distance_count>=0),
  primary_page_id uuid,
  competing_page_count integer NOT NULL DEFAULT 0 CHECK(competing_page_count>=0),
  opportunity_score double precision NOT NULL DEFAULT 0
    CHECK(opportunity_score>=0 AND opportunity_score<=100),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT keyword_cluster_run_tenant_fk FOREIGN KEY(analysis_run_id,tenant_id)
    REFERENCES keyword_analysis_run(id,tenant_id),
  CONSTRAINT keyword_cluster_page_tenant_fk FOREIGN KEY(primary_page_id,tenant_id)
    REFERENCES page(id,tenant_id),
  UNIQUE(analysis_run_id,cluster_key),
  UNIQUE(id,tenant_id)
);
CREATE INDEX keyword_cluster_ranked_idx
  ON keyword_cluster(tenant_id,site_id,analysis_run_id,opportunity_score DESC,cluster_key);

CREATE TABLE keyword_cluster_member (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  cluster_id uuid NOT NULL,
  site_id uuid NOT NULL,
  query_hash text NOT NULL CHECK(length(query_hash)=64),
  clicks double precision NOT NULL DEFAULT 0 CHECK(clicks>=0),
  impressions double precision NOT NULL DEFAULT 0 CHECK(impressions>=0),
  ctr double precision NOT NULL DEFAULT 0 CHECK(ctr>=0 AND ctr<=1),
  position double precision NOT NULL DEFAULT 0 CHECK(position>=0),
  best_page_id uuid,
  PRIMARY KEY(tenant_id,cluster_id,query_hash),
  CONSTRAINT keyword_member_cluster_tenant_fk FOREIGN KEY(cluster_id,tenant_id)
    REFERENCES keyword_cluster(id,tenant_id),
  CONSTRAINT keyword_member_query_fk FOREIGN KEY(tenant_id,site_id,query_hash)
    REFERENCES search_query(tenant_id,site_id,query_hash)
);
CREATE INDEX keyword_cluster_member_cluster_idx
  ON keyword_cluster_member(tenant_id,cluster_id,impressions DESC,query_hash);

ALTER TABLE search_query ENABLE ROW LEVEL SECURITY;
ALTER TABLE keyword_analysis_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE keyword_cluster ENABLE ROW LEVEL SECURITY;
ALTER TABLE keyword_cluster_member ENABLE ROW LEVEL SECURITY;

CREATE POLICY search_query_tenant_isolation ON search_query
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY keyword_analysis_run_tenant_isolation ON keyword_analysis_run
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY keyword_cluster_tenant_isolation ON keyword_cluster
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY keyword_cluster_member_tenant_isolation ON keyword_cluster_member
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
