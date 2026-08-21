import assert from "node:assert/strict";
import test from "node:test";
import {
  DeploymentReceiptSchema,
  GovernanceStatusSchema,
  MeasurementSeriesSchema,
  OpportunitySchema,
  PolicySimulationSchema,
  PostDeployVerificationSchema,
  ProposalSchema,
  RollbackReceiptSchema,
} from "./index.js";

test("rejects opportunities without evidence", () => {
  const result = OpportunitySchema.safeParse({
    id: "019d0000-0000-7000-8000-000000000001",
    type: "technical",
    title: "Fix canonical",
    score: 80,
    risk: "low",
    impact: 0.8,
    confidence: 0.8,
    urgency: 0.8,
    effort: 0.2,
    scoring_version: "v1",
    evidence_refs: [],
  });
  assert.equal(result.success, false);
});

test("validates well-formed proposal", () => {
  const result = ProposalSchema.safeParse({
    id: "019d0000-0000-7000-8000-000000000001",
    site_id: "019d0000-0000-7000-8000-000000000002",
    opportunity_id: "019d0000-0000-7000-8000-000000000003",
    page_id: "019d0000-0000-7000-8000-000000000004",
    author_id: "019d0000-0000-7000-8000-000000000005",
    title: "Update meta description",
    rationale: "Align with search query intent",
    target_type: "html_meta",
    target_path: "/products",
    before_content: "<meta name='description' content='' />",
    after_content: "<meta name='description' content='Best cloud storage services.' />",
    diff_unified: "--- a/products\n+++ b/products\n",
    base_hash: "a".repeat(64),
    proposal_hash: "b".repeat(64),
    risk: "low",
    status: "validated",
    version: 1,
  });
  assert.equal(result.success, true);
});

test("validates deployment receipt", () => {
  const result = DeploymentReceiptSchema.safeParse({
    id: "019d0000-0000-7000-8000-000000000001",
    site_id: "019d0000-0000-7000-8000-000000000002",
    proposal_id: "019d0000-0000-7000-8000-000000000003",
    connector_type: "github",
    idempotency_key: "deploy-test-12345",
    external_ref: "https://github.com/org/repo/pull/1",
    status: "applied",
    deployed_at: new Date().toISOString(),
  });
  assert.equal(result.success, true);
});

test("validates post-deploy verification record", () => {
  const result = PostDeployVerificationSchema.safeParse({
    id: "019d0000-0000-7000-8000-000000000001",
    site_id: "019d0000-0000-7000-8000-000000000002",
    proposal_id: "019d0000-0000-7000-8000-000000000003",
    deployment_receipt_id: "019d0000-0000-7000-8000-000000000004",
    page_id: "019d0000-0000-7000-8000-000000000005",
    status: "verified",
    verified_at: new Date().toISOString(),
    expected_pattern: "Best cloud storage",
    observed_snippet: "<title>Best cloud storage</title>",
    http_status: 200,
    notes: "Rendered verification passed.",
  });
  assert.equal(result.success, true);
});

test("validates measurement series record", () => {
  const result = MeasurementSeriesSchema.safeParse({
    id: "019d0000-0000-7000-8000-000000000001",
    site_id: "019d0000-0000-7000-8000-000000000002",
    proposal_id: "019d0000-0000-7000-8000-000000000003",
    page_id: "019d0000-0000-7000-8000-000000000004",
    baseline_window_start: new Date().toISOString(),
    baseline_window_end: new Date().toISOString(),
    followup_window_start: new Date().toISOString(),
    followup_window_end: new Date().toISOString(),
    baseline_metrics: {clicks: 120, impressions: 2400, ctr: 0.05, position: 14.2},
    followup_metrics: {clicks: 180, impressions: 2900, ctr: 0.062, position: 11.1},
    delta_metrics: {clicks_delta: 60, clicks_pct_change: 50.0, position_delta: 3.1},
    confidence_score: 0.85,
    is_sparse: false,
    annotations: ["Association observed over 28-day window."],
    calculated_at: new Date().toISOString(),
  });
  assert.equal(result.success, true);
});

test("validates governance status and rollback receipt", () => {
  const gov = GovernanceStatusSchema.safeParse({
    site_id: "019d0000-0000-7000-8000-000000000001",
    mode: "autopilot",
    autopilot_enabled: true,
    emergency_freeze: false,
    daily_change_budget: 5,
    today_deployments_count: 2,
    freeze_window_start: null,
    freeze_window_end: null,
  });
  assert.equal(gov.success, true);

  const roll = RollbackReceiptSchema.safeParse({
    id: "019d0000-0000-7000-8000-000000000001",
    site_id: "019d0000-0000-7000-8000-000000000002",
    proposal_id: "019d0000-0000-7000-8000-000000000003",
    deployment_receipt_id: "019d0000-0000-7000-8000-000000000004",
    restored_hash: "a".repeat(64),
    status: "applied",
    rolled_back_at: new Date().toISOString(),
    notes: "Restored previous state.",
  });
  assert.equal(roll.success, true);
});


