BEGIN;

ALTER TABLE opportunity
  ADD CONSTRAINT opportunity_id_tenant_unique UNIQUE(id,tenant_id);

CREATE TABLE calibration_run (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  status text NOT NULL DEFAULT 'open' CHECK(status IN('open','completed','cancelled')),
  strategy text NOT NULL CHECK(strategy IN('top_opportunities_v1')),
  target_size integer NOT NULL CHECK(target_size BETWEEN 1 AND 100),
  scoring_version_id uuid REFERENCES scoring_version(id),
  idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 200),
  request_hash text NOT NULL CHECK(length(request_hash)=64),
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE(tenant_id,site_id,idempotency_key),
  UNIQUE(id,tenant_id)
);

CREATE TABLE calibration_item (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  calibration_run_id uuid NOT NULL,
  opportunity_id uuid NOT NULL,
  page_id uuid NOT NULL REFERENCES page(id),
  ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 100),
  rule_key text NOT NULL,
  evidence_snapshot jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT calibration_item_run_tenant_fk FOREIGN KEY(calibration_run_id,tenant_id)
    REFERENCES calibration_run(id,tenant_id),
  CONSTRAINT calibration_item_opportunity_tenant_fk FOREIGN KEY(opportunity_id,tenant_id)
    REFERENCES opportunity(id,tenant_id),
  UNIQUE(calibration_run_id,ordinal),
  UNIQUE(calibration_run_id,opportunity_id),
  UNIQUE(id,tenant_id)
);

CREATE TABLE calibration_review (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  calibration_item_id uuid NOT NULL,
  reviewer_id uuid NOT NULL,
  accuracy_label text NOT NULL CHECK(accuracy_label IN('true_positive','false_positive','uncertain')),
  actionability text NOT NULL CHECK(actionability IN('accept','edit','dismiss','defer')),
  severity_fit text NOT NULL CHECK(severity_fit IN('appropriate','overstated','understated','uncertain')),
  notes text NOT NULL DEFAULT '' CHECK(char_length(notes)<=1000),
  request_hash text NOT NULL CHECK(length(request_hash)=64),
  idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 200),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT calibration_review_item_tenant_fk FOREIGN KEY(calibration_item_id,tenant_id)
    REFERENCES calibration_item(id,tenant_id),
  UNIQUE(tenant_id,reviewer_id,idempotency_key)
);

CREATE INDEX calibration_run_tenant_site_idx
  ON calibration_run(tenant_id,site_id,status,created_at DESC);
CREATE UNIQUE INDEX calibration_run_one_open_idx
  ON calibration_run(tenant_id,site_id) WHERE status='open';
CREATE INDEX calibration_item_tenant_run_idx
  ON calibration_item(tenant_id,calibration_run_id,ordinal);
CREATE INDEX calibration_review_tenant_item_idx
  ON calibration_review(tenant_id,calibration_item_id,reviewer_id,created_at DESC,id DESC);

ALTER TABLE calibration_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE calibration_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE calibration_review ENABLE ROW LEVEL SECURITY;

CREATE POLICY calibration_run_tenant_isolation ON calibration_run
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY calibration_item_tenant_isolation ON calibration_item
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY calibration_review_tenant_isolation ON calibration_review
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
