-- The workspace chat can now answer through the workspace's own Claude or
-- OpenAI key. Record which model wrote each answer and what it cost, so the
-- spend counts against the same monthly budget as blog drafting.
--
-- Additive, nullable or defaulted; existing rows are deterministic answers
-- with no model and no cost. Rollback: drop the three columns.
BEGIN;

ALTER TABLE agent_message
  ADD COLUMN provider varchar(16),
  ADD COLUMN model varchar(80),
  ADD COLUMN cost_micros bigint NOT NULL DEFAULT 0;

COMMIT;
