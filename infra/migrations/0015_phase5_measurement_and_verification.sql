BEGIN;

CREATE TABLE post_deploy_verification (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  proposal_id uuid NOT NULL,
  deployment_receipt_id uuid NOT NULL,
  page_id uuid NOT NULL REFERENCES page(id),
  status text NOT NULL DEFAULT 'pending' CHECK(status IN('pending','verified','failed','recheck_scheduled')),
  verified_at timestamptz,
  expected_pattern text NOT NULL,
  observed_snippet text,
  http_status integer,
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT verification_proposal_fk FOREIGN KEY(proposal_id, tenant_id) REFERENCES proposal(id, tenant_id),
  CONSTRAINT verification_receipt_fk FOREIGN KEY(deployment_receipt_id, tenant_id) REFERENCES deployment_receipt(id, tenant_id),
  UNIQUE(tenant_id, deployment_receipt_id),
  UNIQUE(id, tenant_id)
);

CREATE INDEX post_deploy_verification_tenant_site_idx
  ON post_deploy_verification(tenant_id, site_id, status);

CREATE TABLE measurement_series (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  proposal_id uuid NOT NULL,
  page_id uuid NOT NULL REFERENCES page(id),
  baseline_window_start date NOT NULL,
  baseline_window_end date NOT NULL,
  followup_window_start date NOT NULL,
  followup_window_end date NOT NULL,
  baseline_metrics jsonb NOT NULL,
  followup_metrics jsonb NOT NULL,
  delta_metrics jsonb NOT NULL,
  confidence_score double precision NOT NULL CHECK(confidence_score>=0 AND confidence_score<=1),
  is_sparse boolean NOT NULL DEFAULT false,
  annotations jsonb NOT NULL DEFAULT '[]'::jsonb,
  calculated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT measurement_proposal_fk FOREIGN KEY(proposal_id, tenant_id) REFERENCES proposal(id, tenant_id),
  UNIQUE(tenant_id, proposal_id, followup_window_end),
  UNIQUE(id, tenant_id)
);

CREATE INDEX measurement_tenant_site_idx ON measurement_series(tenant_id, site_id, calculated_at DESC);

ALTER TABLE post_deploy_verification ENABLE ROW LEVEL SECURITY;
ALTER TABLE measurement_series ENABLE ROW LEVEL SECURITY;

CREATE POLICY post_deploy_verification_tenant_isolation ON post_deploy_verification
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

CREATE POLICY measurement_series_tenant_isolation ON measurement_series
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
