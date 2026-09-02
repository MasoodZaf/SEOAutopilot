BEGIN;

-- The agent workspace is a conversation over the platform's own skills. A
-- message never becomes tool authority: the router selects from a fixed,
-- code-defined skill set and every skill re-checks the actor's role, so chat
-- can only reach what the same actor could already reach through the API.
CREATE TABLE agent_session (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  site_id uuid NOT NULL REFERENCES site(id),
  title text NOT NULL CHECK(char_length(title) BETWEEN 1 AND 200),
  status text NOT NULL DEFAULT 'active' CHECK(status IN('active','archived')),
  message_count integer NOT NULL DEFAULT 0 CHECK(message_count>=0),
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id,tenant_id)
);
CREATE INDEX agent_session_tenant_site_idx
  ON agent_session(tenant_id,site_id,updated_at DESC,id DESC);

CREATE TABLE agent_task (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  session_id uuid,
  site_id uuid NOT NULL REFERENCES site(id),
  skill_key text NOT NULL CHECK(char_length(skill_key) BETWEEN 1 AND 60),
  status text NOT NULL DEFAULT 'queued'
    CHECK(status IN('queued','running','completed','failed','blocked')),
  -- Set when the skill scheduled work rather than answering from stored data.
  routine_run_id uuid,
  result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_code text CHECK(char_length(error_code)<=80),
  requested_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  CONSTRAINT agent_task_session_tenant_fk FOREIGN KEY(session_id,tenant_id)
    REFERENCES agent_session(id,tenant_id) ON DELETE CASCADE,
  UNIQUE(id,tenant_id)
);
CREATE INDEX agent_task_tenant_site_idx
  ON agent_task(tenant_id,site_id,created_at DESC,id DESC);

CREATE TABLE agent_message (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  session_id uuid NOT NULL,
  site_id uuid NOT NULL REFERENCES site(id),
  sequence integer NOT NULL CHECK(sequence>0),
  role text NOT NULL CHECK(role IN('user','agent')),
  body text NOT NULL CHECK(char_length(body) BETWEEN 1 AND 8000),
  skill_key text CHECK(char_length(skill_key)<=60),
  agent_task_id uuid,
  -- References to stored records the answer was built from. An agent message
  -- with no evidence is a routing or refusal message, never an assertion.
  evidence_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT agent_message_session_tenant_fk FOREIGN KEY(session_id,tenant_id)
    REFERENCES agent_session(id,tenant_id) ON DELETE CASCADE,
  CONSTRAINT agent_message_task_tenant_fk FOREIGN KEY(agent_task_id,tenant_id)
    REFERENCES agent_task(id,tenant_id),
  CONSTRAINT agent_message_user_has_no_skill
    CHECK(role='agent' OR (skill_key IS NULL AND agent_task_id IS NULL)),
  UNIQUE(session_id,sequence)
);
CREATE INDEX agent_message_session_idx ON agent_message(tenant_id,session_id,sequence);

ALTER TABLE agent_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_task ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_message ENABLE ROW LEVEL SECURITY;

CREATE POLICY agent_session_tenant_isolation ON agent_session
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY agent_task_tenant_isolation ON agent_task
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY agent_message_tenant_isolation ON agent_message
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
