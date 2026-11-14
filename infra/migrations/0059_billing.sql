-- One subscription per workspace, held by Stripe and mirrored here.
--
-- Every workspace that exists today is comped: nobody who is already here gets
-- a surprise trial clock. Workspaces created after this start a 14-day trial in
-- tenant_provisioning. Nothing is refused until BILLING_ENFORCED is turned on.
--
-- Only ids, status and dates are kept. No card details or raw Stripe payloads.
--
-- Additive. Rollback: drop table billing_event; drop table tenant_billing.
BEGIN;

CREATE TABLE tenant_billing (
  tenant_id uuid PRIMARY KEY REFERENCES tenant(id),
  status text NOT NULL CHECK(status IN(
    'comped','trialing','active','past_due','canceled','unpaid',
    'incomplete','incomplete_expired','paused'
  )),
  stripe_customer_id text UNIQUE,
  stripe_subscription_id text UNIQUE,
  trial_ends_at timestamptz,
  current_period_end timestamptz,
  cancel_at_period_end boolean NOT NULL DEFAULT false,
  -- Stripe does not promise order, so an older event must not undo a newer one.
  last_event_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK(status<>'trialing' OR trial_ends_at IS NOT NULL)
);

ALTER TABLE tenant_billing ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_billing FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_billing_isolation ON tenant_billing
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

-- Stripe redelivers. Each event id is applied once.
CREATE TABLE billing_event (
  stripe_event_id text PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  event_type text NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX billing_event_tenant_idx ON billing_event(tenant_id,received_at DESC);

ALTER TABLE billing_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_event FORCE ROW LEVEL SECURITY;
CREATE POLICY billing_event_isolation ON billing_event
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

INSERT INTO tenant_billing(tenant_id,status) SELECT id,'comped' FROM tenant;

COMMIT;
