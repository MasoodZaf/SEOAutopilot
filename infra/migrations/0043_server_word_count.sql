-- How much of a page exists before JavaScript runs.
--
-- The crawler renders a page in a browser when the server's HTML is a shell,
-- and until now kept only the rendered result. That hid the single biggest
-- visibility problem a client-rendered site has: codearc.net serves 423 pages
-- whose HTML says "Please enable JavaScript" and nothing else, and every
-- observation recorded the few hundred words the browser produced instead.
-- Null means the page was not rendered, so word_count already is this number.
-- Additive and nullable: old crawler builds simply never write it.
BEGIN;

ALTER TABLE page_observation
  ADD COLUMN server_word_count integer CHECK (server_word_count >= 0);

COMMIT;
