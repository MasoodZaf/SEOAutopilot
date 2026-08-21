BEGIN;

CREATE UNIQUE INDEX crawl_job_one_active_per_site_idx
  ON crawl_job(tenant_id,site_id)
  WHERE status IN ('queued','running');

COMMIT;
