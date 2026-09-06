import {z} from "zod";

export const ProductModeSchema = z.enum(["observe", "recommend", "autopilot"]);
export const RiskSchema = z.enum(["low", "medium", "high", "prohibited"]);
export const ProposalStatusSchema = z.enum([
  "draft",
  "validating",
  "validated",
  "review_required",
  "approved",
  "rejected",
  "expired",
  "deploying",
  "deployed",
  "failed",
]);

export const OpportunitySchema = z.object({
  id: z.string().uuid(),
  type: z.string().min(1).max(80),
  title: z.string().min(1).max(240),
  score: z.number().min(0).max(100),
  risk: RiskSchema,
  impact: z.number().min(0).max(1),
  confidence: z.number().min(0).max(1),
  urgency: z.number().min(0).max(1),
  effort: z.number().min(0).max(1),
  scoring_version: z.string(),
  evidence_refs: z.array(z.string().uuid()).min(1),
});
export type Opportunity = z.infer<typeof OpportunitySchema>;

export const ProposalSchema = z.object({
  id: z.string().uuid(),
  site_id: z.string().uuid(),
  opportunity_id: z.string().uuid(),
  page_id: z.string().uuid(),
  author_id: z.string().uuid(),
  title: z.string().min(3).max(240),
  rationale: z.string(),
  target_type: z.enum(["html_meta", "json_ld_schema", "link_insertion", "content_edit", "github_file"]),
  target_path: z.string().min(1).max(1024),
  before_content: z.string(),
  after_content: z.string(),
  diff_unified: z.string(),
  base_hash: z.string().length(64),
  proposal_hash: z.string().length(64),
  risk: RiskSchema,
  status: ProposalStatusSchema,
  version: z.number().int().min(1),
});
export type Proposal = z.infer<typeof ProposalSchema>;

export const DeploymentReceiptSchema = z.object({
  id: z.string().uuid(),
  site_id: z.string().uuid(),
  proposal_id: z.string().uuid(),
  connector_type: z.enum(["github", "cms_staging", "mock"]),
  idempotency_key: z.string().min(8).max(200),
  external_ref: z.string(),
  // `rollback_pending` is a rollback that has been asked for but not carried
  // out: a revert pull request is open and the deployed change is still live.
  // It is deliberately not `rolled_back`, which means the change is gone.
  status: z.enum(["pending", "applied", "failed", "rollback_pending", "rolled_back"]),
  deployed_at: z.string().datetime(),
});
export type DeploymentReceipt = z.infer<typeof DeploymentReceiptSchema>;

export const PostDeployVerificationSchema = z.object({
  id: z.string().uuid(),
  site_id: z.string().uuid(),
  proposal_id: z.string().uuid(),
  deployment_receipt_id: z.string().uuid(),
  page_id: z.string().uuid(),
  status: z.enum(["pending", "verified", "failed", "recheck_scheduled"]),
  verified_at: z.string().datetime().nullable(),
  expected_pattern: z.string(),
  observed_snippet: z.string().nullable(),
  http_status: z.number().int().nullable(),
  notes: z.string(),
});
export type PostDeployVerification = z.infer<typeof PostDeployVerificationSchema>;

export const MeasurementSeriesSchema = z.object({
  id: z.string().uuid(),
  site_id: z.string().uuid(),
  proposal_id: z.string().uuid(),
  page_id: z.string().uuid(),
  baseline_window_start: z.string().datetime(),
  baseline_window_end: z.string().datetime(),
  followup_window_start: z.string().datetime(),
  followup_window_end: z.string().datetime(),
  baseline_metrics: z.record(z.string(), z.any()),
  followup_metrics: z.record(z.string(), z.any()),
  delta_metrics: z.record(z.string(), z.any()),
  confidence_score: z.number().min(0).max(1),
  is_sparse: z.boolean(),
  annotations: z.array(z.string()),
  calculated_at: z.string().datetime(),
});
export type MeasurementSeries = z.infer<typeof MeasurementSeriesSchema>;

export const GovernanceStatusSchema = z.object({
  site_id: z.string().uuid(),
  mode: ProductModeSchema,
  autopilot_enabled: z.boolean(),
  emergency_freeze: z.boolean(),
  daily_change_budget: z.number().int().min(1).max(50),
  today_deployments_count: z.number().int().min(0),
  freeze_window_start: z.string().datetime().nullable(),
  freeze_window_end: z.string().datetime().nullable(),
});
export type GovernanceStatus = z.infer<typeof GovernanceStatusSchema>;

export const PolicySimulationSchema = z.object({
  id: z.string().uuid(),
  site_id: z.string().uuid(),
  evaluated_proposals_count: z.number().int().min(0),
  auto_deployable_count: z.number().int().min(0),
  review_required_count: z.number().int().min(0),
  prohibited_count: z.number().int().min(0),
  simulation_results_json: z.record(z.string(), z.any()),
  run_at: z.string().datetime(),
});
export type PolicySimulation = z.infer<typeof PolicySimulationSchema>;

export const RollbackReceiptSchema = z.object({
  id: z.string().uuid(),
  site_id: z.string().uuid(),
  proposal_id: z.string().uuid(),
  deployment_receipt_id: z.string().uuid(),
  restored_hash: z.string().length(64),
  status: z.enum(["applied", "failed"]),
  rolled_back_at: z.string().datetime(),
  notes: z.string(),
});
export type RollbackReceipt = z.infer<typeof RollbackReceiptSchema>;


