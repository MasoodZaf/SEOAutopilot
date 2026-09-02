export type Skill = {
  key: string;
  name: string;
  description: string;
  effect: "read" | "schedule";
  example: string;
  schedules_work: boolean;
};

export type AgentSession = {
  id: string;
  site_id: string;
  title: string;
  status: string;
  message_count: number;
  created_at: string;
  updated_at: string;
};

export type AgentMessage = {
  id: string;
  session_id: string;
  sequence: number;
  role: "user" | "agent";
  body: string;
  skill_key: string | null;
  agent_task_id: string | null;
  evidence_json: Array<Record<string, unknown>>;
  created_at: string;
};

export type AgentTask = {
  id: string;
  session_id: string | null;
  skill_key: string;
  status: string;
  routine_run_id: string | null;
  error_code: string | null;
  created_at: string;
  finished_at: string | null;
};

export type Routine = {
  id: string;
  kind: string;
  cadence: string;
  schedule_hour_utc: number;
  schedule_isodow: number | null;
  enabled: boolean;
  next_run_at: string;
  last_run_at: string | null;
  last_status: string | null;
  consecutive_failures: number;
};

export type RoutineRun = {
  id: string;
  kind: string;
  status: string;
  trigger: string;
  scheduled_for: string;
  finished_at: string | null;
  skip_reason: string | null;
  error_code: string | null;
  summary_json: Record<string, unknown>;
};

export type ReportSummary = {
  id: string;
  kind: string;
  period_start: string;
  period_end: string;
  generated_at: string;
  content_hash: string;
};

export type KeywordCluster = {
  id: string;
  label: string;
  intent: string;
  answer_engine_candidate: boolean;
  member_count: number;
  impressions: number;
  ctr: number;
  average_position: number | null;
  striking_distance_count: number;
  competing_page_count: number;
  opportunity_score: number;
};

export type ContentBrief = {
  id: string;
  kind: "refresh" | "new_page";
  status: string;
  cluster_label: string;
  intent: string;
  answer_engine_candidate: boolean;
  priority_score: number;
};

export type AiVisibility = {
  id: string;
  captured_on: string;
  readiness_score: number;
  citation_source: string;
  factors_json: Record<string, unknown>;
};
