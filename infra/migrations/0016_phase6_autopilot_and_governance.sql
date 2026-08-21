BEGIN;

ALTER TABLE site
  ADD COLUMN IF NOT EXISTS autopilot_enabled boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS emergency_freeze boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS daily_change_budget integer NOT NULL DEFAULT 5 CHECK(daily_change_budget BETWEEN 1 AND 50),
  ADD COLUMN IF NOT EXISTS freeze_window_start timestamptz,
  ADD COLUMN IF NOT EXISTS freeze_window_end timestamptz;

CREATE TABLE policy_simulation_run (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  evaluated_proposals_count integer NOT NULL,
  auto_deployable_count integer NOT NULL,
  review_required_count integer NOT NULL,
  prohibited_count integer NOT NULL,
  simulation_results_json jsonb NOT NULL,
  run_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id, tenant_id)
);

CREATE INDEX simulation_tenant_site_idx ON policy_simulation_run(tenant_id, site_id, run_at DESC);

CREATE TABLE rollback_receipt (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  proposal_id uuid NOT NULL,
  deployment_receipt_id uuid NOT NULL,
  restored_hash text NOT NULL CHECK(length(restored_hash)=64),
  status text NOT NULL DEFAULT 'applied' CHECK(status IN('applied','failed')),
  rolled_back_at timestamptz NOT NULL DEFAULT now(),
  notes text NOT NULL DEFAULT '',
  CONSTRAINT rollback_proposal_fk FOREIGN KEY(proposal_id, tenant_id) REFERENCES proposal(id, tenant_id),
  CONSTRAINT rollback_receipt_fk FOREIGN KEY(deployment_receipt_id, tenant_id) REFERENCES deployment_receipt(id, tenant_id),
  UNIQUE(id, tenant_id)
);

CREATE INDEX rollback_tenant_site_idx ON rollback_receipt(tenant_id, site_id, rolled_back_at DESC);

ALTER TABLE policy_simulation_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE rollback_receipt ENABLE ROW LEVEL SECURITY;

CREATE POLICY policy_simulation_tenant_isolation ON policy_simulation_run
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

CREATE POLICY rollback_receipt_tenant_isolation ON rollback_receipt
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
