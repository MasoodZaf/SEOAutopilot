BEGIN;

-- Scheduled routines. A routine only produces evidence, reports, and advisory
-- artifacts; it never deploys. Deployment authority stays with the governed
-- proposal lifecycle and its fail-closed checks.
CREATE TABLE routine (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  kind text NOT NULL CHECK(kind IN(
    'site_audit','keyword_refresh','sitemap_coverage',
    'competitor_scan','ai_visibility_scan','weekly_report'
  )),
  cadence text NOT NULL CHECK(cadence IN('daily','weekly','monthly')),
  schedule_hour_utc smallint NOT NULL DEFAULT 6 CHECK(schedule_hour_utc BETWEEN 0 AND 23),
  schedule_minute_utc smallint NOT NULL DEFAULT 0 CHECK(schedule_minute_utc BETWEEN 0 AND 59),
  -- ISO weekday, 1=Monday..7=Sunday. Required for weekly, ignored otherwise.
  schedule_isodow smallint CHECK(schedule_isodow BETWEEN 1 AND 7),
  -- Day of month capped at 28 so every month has the date.
  schedule_dom smallint CHECK(schedule_dom BETWEEN 1 AND 28),
  enabled boolean NOT NULL DEFAULT true,
  next_run_at timestamptz NOT NULL,
  last_run_at timestamptz,
  last_status text CHECK(last_status IN('completed','failed','skipped')),
  consecutive_failures integer NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by uuid NOT NULL,
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,site_id,kind),
  UNIQUE(id,tenant_id),
  CONSTRAINT routine_weekly_isodow_check
    CHECK(cadence <> 'weekly' OR schedule_isodow IS NOT NULL),
  CONSTRAINT routine_monthly_dom_check
    CHECK(cadence <> 'monthly' OR schedule_dom IS NOT NULL)
);
CREATE INDEX routine_due_idx ON routine(next_run_at) WHERE enabled;
CREATE INDEX routine_tenant_site_idx ON routine(tenant_id,site_id,kind);

CREATE TABLE routine_run (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  routine_id uuid NOT NULL,
  site_id uuid NOT NULL REFERENCES site(id),
  kind text NOT NULL,
  status text NOT NULL DEFAULT 'queued'
    CHECK(status IN('queued','running','completed','failed','skipped')),
  trigger text NOT NULL DEFAULT 'schedule' CHECK(trigger IN('schedule','manual')),
  scheduled_for timestamptz NOT NULL,
  started_at timestamptz,
  finished_at timestamptz,
  lease_until timestamptz,
  attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
  skip_reason text CHECK(char_length(skip_reason)<=80),
  error_code text CHECK(char_length(error_code)<=80),
  summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT routine_run_routine_tenant_fk FOREIGN KEY(routine_id,tenant_id)
    REFERENCES routine(id,tenant_id),
  -- One run per routine per scheduled instant: the scheduler is idempotent
  -- across restarts and multiple worker replicas.
  UNIQUE(routine_id,scheduled_for),
  UNIQUE(id,tenant_id)
);
CREATE INDEX routine_run_tenant_site_idx
  ON routine_run(tenant_id,site_id,created_at DESC,id DESC);
CREATE INDEX routine_run_claimable_idx ON routine_run(status,scheduled_for)
  WHERE status IN('queued','running');

CREATE TABLE report (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  routine_run_id uuid,
  kind text NOT NULL CHECK(kind IN(
    'weekly_digest','audit_summary','competitor_digest','ai_visibility_digest'
  )),
  period_start date NOT NULL,
  period_end date NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  scoring_version_id uuid REFERENCES scoring_version(id),
  content_hash text NOT NULL CHECK(length(content_hash)=64),
  payload_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT report_period_check CHECK(period_end >= period_start),
  CONSTRAINT report_run_tenant_fk FOREIGN KEY(routine_run_id,tenant_id)
    REFERENCES routine_run(id,tenant_id),
  UNIQUE(tenant_id,site_id,kind,period_start,period_end),
  UNIQUE(id,tenant_id)
);
CREATE INDEX report_tenant_site_idx
  ON report(tenant_id,site_id,generated_at DESC,id DESC);

-- Outbound notification targets. The destination secret (webhook URL, token)
-- is held in the same AES-256-GCM envelope shape as connector secrets; the
-- managed-secret adapter remains a production release gate.
CREATE TABLE notification_channel (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid REFERENCES site(id),
  kind text NOT NULL CHECK(kind IN('slack_webhook','generic_webhook')),
  name text NOT NULL CHECK(char_length(name) BETWEEN 1 AND 120),
  enabled boolean NOT NULL DEFAULT true,
  destination_hint text NOT NULL CHECK(char_length(destination_hint)<=200),
  ciphertext bytea NOT NULL,
  nonce bytea NOT NULL CHECK(octet_length(nonce)=12),
  aad_hash text NOT NULL CHECK(length(aad_hash)=64),
  key_version text NOT NULL,
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  UNIQUE(id,tenant_id)
);
CREATE INDEX notification_channel_tenant_idx
  ON notification_channel(tenant_id,site_id) WHERE revoked_at IS NULL;

CREATE TABLE notification_delivery (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  channel_id uuid NOT NULL,
  report_id uuid NOT NULL,
  status text NOT NULL DEFAULT 'queued'
    CHECK(status IN('queued','delivered','failed','blocked')),
  attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
  lease_until timestamptz,
  delivered_at timestamptz,
  error_code text CHECK(char_length(error_code)<=80),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT notification_delivery_channel_tenant_fk FOREIGN KEY(channel_id,tenant_id)
    REFERENCES notification_channel(id,tenant_id),
  CONSTRAINT notification_delivery_report_tenant_fk FOREIGN KEY(report_id,tenant_id)
    REFERENCES report(id,tenant_id),
  UNIQUE(channel_id,report_id)
);
CREATE INDEX notification_delivery_claimable_idx
  ON notification_delivery(status,created_at) WHERE status='queued';

ALTER TABLE routine ENABLE ROW LEVEL SECURITY;
ALTER TABLE routine_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE report ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_channel ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_delivery ENABLE ROW LEVEL SECURITY;

CREATE POLICY routine_tenant_isolation ON routine
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY routine_run_tenant_isolation ON routine_run
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY report_tenant_isolation ON report
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY notification_channel_tenant_isolation ON notification_channel
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY notification_delivery_tenant_isolation ON notification_delivery
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
