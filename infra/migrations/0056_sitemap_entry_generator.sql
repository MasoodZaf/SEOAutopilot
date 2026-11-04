-- Proposals that list new blog posts in a plain-HTML site's sitemap.xml.
--
-- One live per site, like llms.txt: a newer post's entry replaces an older
-- one that nobody approved yet, so two edits of the same file never race.
-- Sitemap edits stay high risk and are always merged on GitHub by a person.
--
-- Rollback: restore the check to generator IN('llms_txt') once no
-- 'sitemap_entry' proposal remains.
BEGIN;

ALTER TABLE proposal DROP CONSTRAINT proposal_generator_check;
ALTER TABLE proposal ADD CONSTRAINT proposal_generator_check
  CHECK(generator IS NULL OR generator IN('llms_txt','sitemap_entry'));

COMMIT;
