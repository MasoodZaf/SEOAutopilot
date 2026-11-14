-- A domain can also be proved with a file or a home-page meta tag. DNS still works.
--
-- Additive. Rollback: mark any file or meta_tag challenge as dns_txt, then restore the check.
BEGIN;
ALTER TABLE site_verification_challenge DROP CONSTRAINT site_verification_challenge_method_check;
ALTER TABLE site_verification_challenge ADD CONSTRAINT site_verification_challenge_method_check
  CHECK (method IN ('dns_txt','file','meta_tag'));
COMMIT;
