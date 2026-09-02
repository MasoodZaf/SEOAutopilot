BEGIN;

CREATE TABLE proposal (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  opportunity_id uuid NOT NULL REFERENCES opportunity(id),
  page_id uuid NOT NULL REFERENCES page(id),
  author_id uuid NOT NULL,
  title text NOT NULL CHECK(char_length(title) BETWEEN 3 AND 240),
  rationale text NOT NULL,
  target_type text NOT NULL CHECK(target_type IN('html_meta','json_ld_schema','link_insertion','content_edit','github_file')),
  target_path text NOT NULL CHECK(char_length(target_path) BETWEEN 1 AND 1024),
  before_content text NOT NULL,
  after_content text NOT NULL,
  diff_unified text NOT NULL,
  base_hash text NOT NULL CHECK(length(base_hash)=64),
  proposal_hash text NOT NULL CHECK(length(proposal_hash)=64),
  risk text NOT NULL CHECK(risk IN('low','medium','high','prohibited')),
  status text NOT NULL DEFAULT 'draft'
    CHECK(status IN('draft','validating','validated','review_required','approved','rejected','expired','deploying','deployed','failed')),
  validations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  policy_evaluation_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence_refs jsonb NOT NULL DEFAULT '{}'::jsonb,
  expires_at timestamptz NOT NULL,
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id, tenant_id)
);

CREATE INDEX proposal_tenant_site_status_idx ON proposal(tenant_id, site_id, status, created_at DESC);
CREATE INDEX proposal_tenant_opportunity_idx ON proposal(tenant_id, opportunity_id);

CREATE TABLE proposal_approval (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  proposal_id uuid NOT NULL,
  proposal_version integer NOT NULL,
  approver_id uuid NOT NULL,
  decision text NOT NULL CHECK(decision IN('approved','rejected')),
  notes text NOT NULL DEFAULT '',
  decided_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT proposal_approval_proposal_fk FOREIGN KEY(proposal_id, tenant_id) REFERENCES proposal(id, tenant_id),
  UNIQUE(tenant_id, proposal_id, proposal_version, approver_id)
);

CREATE INDEX proposal_approval_tenant_proposal_idx ON proposal_approval(tenant_id, proposal_id, decided_at DESC);

CREATE TABLE deployment_receipt (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  proposal_id uuid NOT NULL,
  connector_type text NOT NULL CHECK(connector_type IN('github','cms_staging','mock')),
  idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 200),
  external_ref text NOT NULL,
  manifest_json jsonb NOT NULL,
  status text NOT NULL DEFAULT 'applied' CHECK(status IN('pending','applied','failed','rolled_back')),
  deployed_at timestamptz NOT NULL DEFAULT now(),
  verified_at timestamptz,
  CONSTRAINT deployment_receipt_proposal_fk FOREIGN KEY(proposal_id, tenant_id) REFERENCES proposal(id, tenant_id),
  UNIQUE(tenant_id, idempotency_key),
  UNIQUE(id, tenant_id)
);

CREATE INDEX deployment_receipt_tenant_site_idx ON deployment_receipt(tenant_id, site_id, deployed_at DESC);

ALTER TABLE proposal ENABLE ROW LEVEL SECURITY;
ALTER TABLE proposal_approval ENABLE ROW LEVEL SECURITY;
ALTER TABLE deployment_receipt ENABLE ROW LEVEL SECURITY;

CREATE POLICY proposal_tenant_isolation ON proposal
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

CREATE POLICY proposal_approval_tenant_isolation ON proposal_approval
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

CREATE POLICY deployment_receipt_tenant_isolation ON deployment_receipt
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
