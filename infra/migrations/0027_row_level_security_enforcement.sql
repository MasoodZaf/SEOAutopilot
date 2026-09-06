BEGIN;

-- Row-level security was enabled on every tenant table from migration 0001
-- onward, and every policy is written correctly, but none of them has ever been
-- in effect. Two independent conditions caused that, and both are addressed
-- here plus one operational step:
--
--   1. No table set FORCE ROW LEVEL SECURITY. The services connect as the role
--      that runs these migrations, so it owns every table, and an owner bypasses
--      its own policies unless they are forced.
--   2. That role is a SUPERUSER, which ignores row security unconditionally.
--      FORCE does not apply to a superuser, so forcing alone changes nothing --
--      this was measured before writing the migration.
--
-- Forcing is therefore necessary but not sufficient. The services must also stop
-- connecting as a superuser, which is why the application role below exists. The
-- role is created without a password; granting it LOGIN and a secret is an
-- operational step so no credential enters the repository. See DEPLOYMENT.md.

-- Driven from the catalogue rather than a hand-written list, because the defect
-- being fixed is precisely that a table can be missed and look protected.
DO $$
DECLARE target record;
BEGIN
  FOR target IN
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
  LOOP
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', target.relname);
  END LOOP;
END $$;

-- The application role. NOBYPASSRLS is the point of it; NOSUPERUSER is what
-- makes NOBYPASSRLS meaningful. It receives DML only: schema changes stay with
-- the migration role.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'seo_autopilot_app') THEN
    CREATE ROLE seo_autopilot_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  ELSE
    ALTER ROLE seo_autopilot_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO seo_autopilot_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO seo_autopilot_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO seo_autopilot_app;

-- Tables added by later migrations inherit the same grants, so a new table
-- cannot silently become unreachable to the services.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO seo_autopilot_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO seo_autopilot_app;

-- The OAuth callback arrives from Google with no session and no tenant: the
-- tenant is derived *from* the state row, so that one lookup cannot be tenant
-- scoped. It gets a narrow exception instead of an unscoped connection --
-- SELECT only, on this table only, and only while the callback GUC is set. The
-- state value is a 256-bit unguessable secret that is single-use and expiring,
-- and the service sets app.tenant_id from the row it finds before touching
-- anything else, so the unscoped window is one read of one row.
CREATE POLICY connector_oauth_state_callback_lookup ON connector_oauth_state
  FOR SELECT
  USING (current_setting('app.oauth_callback', true) = 'on');

-- The outbox relay and the routine scheduler sweep work across tenants before
-- any tenant scope exists, so they cannot run under a tenant-scoped role. They
-- get a separate identity that is explicit about the exemption rather than
-- reaching for the superuser. It is also NOLOGIN until an operator grants it a
-- secret.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'seo_autopilot_relay') THEN
    CREATE ROLE seo_autopilot_relay NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
  ELSE
    ALTER ROLE seo_autopilot_relay NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO seo_autopilot_relay;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO seo_autopilot_relay;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO seo_autopilot_relay;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO seo_autopilot_relay;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO seo_autopilot_relay;

COMMIT;
