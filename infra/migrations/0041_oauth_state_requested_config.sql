BEGIN;

-- What an installation was asked to configure, held until it is real.
--
-- Starting a GitHub App install used to write the requested branch and path
-- template straight onto the connector, and mark a working connector
-- `reauthorization_required`, before GitHub had sent anyone back. An install
-- abandoned on GitHub's page -- a closed tab, a wrong account -- therefore left
-- a site that had been deploying with a stored token unable to deploy, and
-- deploying to a path nobody had confirmed. The request now waits here, on the
-- single-use state row, and is applied only when the callback completes.
ALTER TABLE connector_oauth_state
  ADD COLUMN requested_config jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
