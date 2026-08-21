BEGIN;

INSERT INTO scoring_version(id, code_version, factor_config_json)
VALUES(
  '019d0000-0000-7000-8000-000000000091',
  'multiagent-v1',
  '{"formula":"impact*confidence*urgency/(0.25+0.75*effort)*risk","risk":{"low":1.0,"medium":0.75,"high":0.35,"prohibited":0.0},"agents":["technical","content","keyword","internal_linking","performance","geo_visibility"]}'::jsonb
) ON CONFLICT (code_version) DO NOTHING;

ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS suppressed_at timestamptz;
ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS suppressed_by uuid;

CREATE INDEX IF NOT EXISTS opportunity_tenant_type_status_idx
  ON opportunity(tenant_id, site_id, type, status);

COMMIT;
