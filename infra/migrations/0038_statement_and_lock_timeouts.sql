BEGIN;

-- Bound how long a statement, a lock wait, or an abandoned transaction can last.
--
-- Every timeout on this database is 0. That was survivable while nothing took a
-- row lock; membership changes now read the row they modify `FOR UPDATE` and
-- lock the owner rows they count, which is what makes two owners demoting each
-- other at the same moment safe. The cost of a lock is that something can wait
-- on it, and with `lock_timeout = 0` that wait is unbounded: one request that
-- dies holding a transaction open blocks every later membership change until a
-- person notices and kills the backend.
--
-- The two roles get different numbers because they do different work, and using
-- one value for both would mean choosing between breaking a sweep and leaving
-- the API unbounded.

-- The application role: API requests, and the crawler's end-of-crawl batch.
-- `statement_timeout` is per statement, and both of those issue many small ones
-- -- a 500-page crawl is 500 upserts, not one long insert -- so a minute is far
-- past anything legitimate while still ending a runaway scan.
-- `idle_in_transaction_session_timeout` is the one that matters here: it is the
-- only thing that eventually releases a lock held by a request nobody is
-- waiting on any more.
ALTER ROLE seo_autopilot_app SET statement_timeout = '60s';
ALTER ROLE seo_autopilot_app SET lock_timeout = '10s';
ALTER ROLE seo_autopilot_app SET idle_in_transaction_session_timeout = '120s';

-- The sweep role holds a connection open across provider calls: the rollback
-- reconciler takes a session advisory lock, then asks GitHub about each pending
-- revert, and SQLAlchemy has an implicit transaction open the whole time. A
-- short idle timeout would kill that connection mid-sweep and drop the lock, so
-- this one is deliberately generous -- it exists to catch a sweep that has hung,
-- not to police one that is waiting on a slow provider.
ALTER ROLE seo_autopilot_relay SET statement_timeout = '120s';
ALTER ROLE seo_autopilot_relay SET lock_timeout = '15s';
ALTER ROLE seo_autopilot_relay SET idle_in_transaction_session_timeout = '900s';

COMMIT;
