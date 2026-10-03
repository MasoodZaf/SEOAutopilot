-- Publishing from SEO Autopilot: merging an approved change's pull request
-- from the dashboard, in one click, instead of on GitHub.
--
-- Until now the GitHub adapter only ever opened pull requests; a person merged
-- them on GitHub. That stays the default. An owner or admin can switch a site
-- to allow publishing from the app; every deployment gate still applies, the
-- pull request must hold exactly the reviewed content, and GitHub's own branch
-- protection is never bypassed. See docs/adr for the decision.
--
-- Additive, off for every existing site. Rollback: drop the column.
BEGIN;

ALTER TABLE site ADD COLUMN publish_from_app boolean NOT NULL DEFAULT false;

COMMIT;
