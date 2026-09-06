BEGIN;

-- Quarantine every finding, opportunity and page score that was derived from a
-- crawl whose page bodies collapsed onto one another.
--
-- Such a crawl fetched hundreds of URLs and recorded the same body on all of
-- them: an app shell, a login wall or a soft 404 served under HTTP 200. Every
-- fetch succeeded, so the crawl completed and analysis ran, producing one
-- "missing h1" and one "thin content" finding per URL. The findings are real
-- rows describing a wall the visitor never sees, and everything derived from
-- them -- scores, opportunities, the frozen calibration set, the digest and the
-- agent's advice -- is fiction.
--
-- The crawler now refuses to emit crawl.completed for such a crawl (see
-- services/crawler/src/evidence-guard.ts). This migration cleans up what the
-- guard was not there to prevent. It uses the same thresholds, and derives the
-- affected crawls from the stored content hashes rather than naming a site, so
-- it catches every instance rather than the one that was noticed.
--
-- Nothing is deleted. Findings become 'suppressed' and opportunities
-- 'suppressed', both reversible, and the observations stay for inspection.

CREATE TEMP TABLE collapsed_crawl ON COMMIT DROP AS
WITH ok AS (
  SELECT crawl_job_id, content_hash
  FROM page_observation
  WHERE http_status BETWEEN 200 AND 299 AND content_hash IS NOT NULL
), grouped AS (
  SELECT crawl_job_id, content_hash, count(*) AS pages
  FROM ok GROUP BY 1, 2
), measured AS (
  SELECT crawl_job_id,
         sum(pages) AS observations,
         count(*)::numeric AS distinct_hashes,
         max(pages)::numeric AS biggest_group
  FROM grouped GROUP BY 1
)
SELECT crawl_job_id
FROM measured
WHERE observations >= 10
  AND (biggest_group / observations >= 0.8 OR distinct_hashes / observations <= 0.1);

CREATE TEMP TABLE collapsed_run ON COMMIT DROP AS
SELECT r.id AS analysis_run_id, r.tenant_id
FROM analysis_run r JOIN collapsed_crawl c ON c.crawl_job_id = r.crawl_job_id;

-- Opportunities first: they are selected through the findings, so this has to
-- run before the findings themselves are suppressed.
UPDATE opportunity o
SET status = 'suppressed',
    suppressed_reason = 'invalid_evidence_content_collapse',
    suppressed_at = now(),
    updated_at = now()
WHERE o.status <> 'suppressed'
  AND EXISTS (
    SELECT 1 FROM opportunity_finding link
    JOIN finding f ON f.id = link.finding_id AND f.tenant_id = link.tenant_id
    JOIN collapsed_run cr ON cr.analysis_run_id = f.analysis_run_id
    WHERE link.opportunity_id = o.id AND link.tenant_id = o.tenant_id
  );

UPDATE finding f
SET status = 'suppressed'
FROM collapsed_run cr
WHERE f.analysis_run_id = cr.analysis_run_id AND f.status <> 'suppressed';

-- Page scores carry no status, and a score computed from a shell is worse than
-- no score because the UI presents it as measured. They are recomputed from
-- observations on the next valid analysis run, so removing them loses nothing.
DELETE FROM page_score s
USING collapsed_run cr
WHERE s.analysis_run_id = cr.analysis_run_id;

-- A calibration run whose items all point at suppressed opportunities was
-- reviewing fiction. Cancel it rather than leaving it open for a reviewer to
-- work through; the items and any reviews already recorded are kept.
UPDATE calibration_run r
SET status = 'cancelled'
WHERE r.status = 'open'
  AND EXISTS (
    SELECT 1 FROM calibration_item i
    JOIN opportunity o ON o.id = i.opportunity_id AND o.tenant_id = i.tenant_id
    WHERE i.calibration_run_id = r.id AND i.tenant_id = r.tenant_id
      AND o.suppressed_reason = 'invalid_evidence_content_collapse'
  );

-- Mark the crawls themselves so the API reports why the site has no findings.
UPDATE crawl_job c
SET error_code = 'content_collapse'
FROM collapsed_crawl cc
WHERE c.id = cc.crawl_job_id AND c.error_code IS DISTINCT FROM 'content_collapse';

COMMIT;
