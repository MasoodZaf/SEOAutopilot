from datetime import UTC, date, datetime
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductMode(StrEnum):
    OBSERVE = "observe"
    RECOMMEND = "recommend"
    AUTOPILOT = "autopilot"


def normalize_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("canonical_origin must be an http(s) origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("credentials, query, and fragment are not allowed")
    if parsed.path not in {"", "/"}:
        raise ValueError("canonical_origin must not contain a path")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost"} or hostname.endswith(".local"):
        raise ValueError("local hosts are not allowed")
    port = parsed.port
    if port and not (
        (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    ):
        raise ValueError("nonstandard ports are not allowed")
    return urlunsplit((parsed.scheme.lower(), hostname, "", "", ""))


class SiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    canonical_origin: str
    mode: ProductMode = ProductMode.OBSERVE

    @field_validator("canonical_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return normalize_origin(value)


class SiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    canonical_origin: str
    normalized_host: str
    mode: ProductMode
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class SiteCollection(BaseModel):
    data: list[SiteRead]
    meta: dict[str, str]


class SiteEnvelope(BaseModel):
    data: SiteRead
    meta: dict[str, str]


class VerificationChallengeRead(BaseModel):
    id: UUID
    method: str
    record_name: str
    record_value: str
    token: str
    expires_at: datetime


class VerificationChallengeEnvelope(BaseModel):
    data: VerificationChallengeRead
    meta: dict[str, str]


class SiteVerify(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class CrawlCreate(BaseModel):
    kind: str = Field(default="full", pattern="^(full|incremental)$")
    max_pages: int = Field(default=500, ge=1, le=10_000)
    max_depth: int = Field(default=10, ge=1, le=50)
    render_policy: str = Field(default="auto", pattern="^(auto|always|never)$")


class CrawlRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    kind: str
    status: str
    config_snapshot: dict[str, object]
    result_summary: dict[str, object]
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    created_at: datetime


class CrawlEnvelope(BaseModel):
    data: CrawlRead
    meta: dict[str, str]


class SearchPerformanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    range_start: date
    range_end: date
    rows: int
    clicks: float
    impressions: float
    ctr: float | None
    position: float | None
    is_sparse: bool


class SearchPerformanceEnvelope(BaseModel):
    data: SearchPerformanceRead
    meta: dict[str, str]


class PerformanceObservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    observed_at: datetime
    strategy: str
    source: str
    lighthouse_version: str
    performance_score: int
    lcp_ms: float | None
    inp_ms: float | None
    cls: float | None
    ttfb_ms: float | None


class PerformanceRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    page_id: UUID
    crawl_job_id: UUID
    status: str
    strategy: str
    source: str
    target_url: str
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    created_at: datetime
    observation: PerformanceObservationRead | None = None


class PerformanceRunEnvelope(BaseModel):
    data: PerformanceRunRead
    meta: dict[str, str]


class PerformanceSummaryRead(BaseModel):
    page_id: UUID
    target_url: str
    strategy: str
    source: str
    sample_count: int
    required_sample_count: int
    status: str
    first_observed_at: datetime
    last_observed_at: datetime
    median_performance_score: float | None
    median_lcp_ms: float | None
    median_inp_ms: float | None
    median_cls: float | None
    median_ttfb_ms: float | None


class PerformanceSummaryEnvelope(BaseModel):
    data: PerformanceSummaryRead
    meta: dict[str, str]


class TechnicalFindingRead(BaseModel):
    id: UUID
    code: str
    severity: str
    summary: str
    confidence: float
    evidence_refs: dict[str, object]


class PageObservationRead(BaseModel):
    id: UUID
    crawl_job_id: UUID
    observed_at: datetime
    http_status: int | None
    final_url: str
    title: str | None
    meta_description: str | None
    h1: list[str]
    word_count: int
    content_hash: str | None
    rendered: bool
    canonical_url: str | None
    robots_directives: list[str]
    structured_data: list[object]
    link_count_total: int
    links_truncated: bool


class PageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    normalized_url: str
    lifecycle_status: str
    first_seen_at: datetime
    last_seen_at: datetime


class PageCollection(BaseModel):
    data: list[PageRead]
    meta: dict[str, str | int | None]


class PageDetail(PageRead):
    latest_observation: PageObservationRead | None
    technical_score: int | None
    scoring_version: str | None
    score_calculated_at: datetime | None
    score_evidence_cutoff: datetime | None
    findings: list[TechnicalFindingRead]


class PageEnvelope(BaseModel):
    data: PageDetail
    meta: dict[str, str]


class OpportunityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    page_id: UUID
    type: str
    title: str
    status: str
    impact: float
    confidence: float
    urgency: float
    effort: float
    risk: str
    score: float
    scoring_version_id: UUID
    evidence_refs: dict[str, object]
    suppressed_reason: str | None = None
    suppressed_at: datetime | None = None
    suppressed_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class OpportunitySuppress(BaseModel):
    reason: str = Field(pattern="^(duplicate|intentional_design|out_of_scope|external_fix|false_positive|other)$")
    notes: str = Field(default="", max_length=1000)

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str) -> str:
        return value.strip()


class OpportunityCollection(BaseModel):
    data: list[OpportunityRead]
    meta: dict[str, str | int]


class OpportunityEnvelope(BaseModel):
    data: OpportunityRead
    meta: dict[str, str]


class CalibrationRunCreate(BaseModel):
    target_size: int = Field(default=20, ge=1, le=100)


class CalibrationReviewCreate(BaseModel):
    accuracy_label: str = Field(pattern="^(true_positive|false_positive|uncertain)$")
    actionability: str = Field(pattern="^(accept|edit|dismiss|defer)$")
    severity_fit: str = Field(pattern="^(appropriate|overstated|understated|uncertain)$")
    notes: str = Field(default="", max_length=1000)

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str) -> str:
        return value.strip()


class CalibrationReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    calibration_item_id: UUID
    reviewer_id: UUID
    accuracy_label: str
    actionability: str
    severity_fit: str
    notes: str
    created_at: datetime


class CalibrationItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    calibration_run_id: UUID
    opportunity_id: UUID
    page_id: UUID
    ordinal: int
    rule_key: str
    evidence_snapshot: dict[str, object]
    current_review: CalibrationReviewRead | None = None


class CalibrationSummary(BaseModel):
    target_size: int
    reviewed: int
    true_positive: int
    false_positive: int
    uncertain: int
    precision: float | None
    actionable: int


class CalibrationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    status: str
    strategy: str
    target_size: int
    scoring_version_id: UUID | None
    created_at: datetime
    items: list[CalibrationItemRead]
    summary: CalibrationSummary


class CalibrationRunEnvelope(BaseModel):
    data: CalibrationRunRead
    meta: dict[str, str]


class CalibrationReviewEnvelope(BaseModel):
    data: CalibrationReviewRead
    meta: dict[str, str]


class CalibrationItemEnvelope(BaseModel):
    data: CalibrationItemRead
    meta: dict[str, str]


class ConnectorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    type: str
    status: str
    external_account_ref: str | None
    granted_scopes: list[str]
    last_sync_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class ConnectorAuthorizationCreate(BaseModel):
    property_ref: str = Field(min_length=1, max_length=2048)

    @field_validator("property_ref")
    @classmethod
    def validate_property_ref(cls, value: str) -> str:
        candidate = value.strip()
        if candidate.startswith("sc-domain:"):
            domain = candidate.removeprefix("sc-domain:").rstrip(".").lower()
            if not domain or "/" in domain or ":" in domain:
                raise ValueError("invalid Search Console domain property")
            return f"sc-domain:{domain}"
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid Search Console URL-prefix property")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("credentials, query, and fragment are not allowed")
        return candidate


class ConnectorCollection(BaseModel):
    data: list[ConnectorRead]
    meta: dict[str, str | int]


class ConnectorAuthorizationRead(BaseModel):
    connector: ConnectorRead
    authorization_url: str
    expires_at: datetime


class ConnectorAuthorizationEnvelope(BaseModel):
    data: ConnectorAuthorizationRead
    meta: dict[str, str]


class ConnectorSyncCreate(BaseModel):
    kind: str = Field(default="incremental", pattern="^(backfill|incremental)$")
    range_start: date
    range_end: date

    @field_validator("range_end")
    @classmethod
    def validate_range_end(cls, value: date) -> date:
        if value > datetime.now(UTC).date():
            raise ValueError("range_end must not be in the future")
        return value


class ConnectorSyncRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    connector_id: UUID
    kind: str
    range_start: date
    range_end: date
    status: str
    counts_json: dict[str, object]
    error_code: str | None
    created_at: datetime


class ConnectorSyncEnvelope(BaseModel):
    data: ConnectorSyncRead
    meta: dict[str, str]


class ProposalCreate(BaseModel):
    opportunity_id: UUID
    page_id: UUID
    title: str = Field(min_length=3, max_length=240)
    rationale: str = Field(min_length=3)
    target_type: str = Field(pattern="^(html_meta|json_ld_schema|link_insertion|content_edit|github_file)$")
    target_path: str = Field(min_length=1, max_length=1024)
    before_content: str
    after_content: str
    expires_in_days: int = Field(default=14, ge=1, le=90)


class ProposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    opportunity_id: UUID
    page_id: UUID
    author_id: UUID
    title: str
    rationale: str
    target_type: str
    target_path: str
    before_content: str
    after_content: str
    diff_unified: str
    base_hash: str
    proposal_hash: str
    risk: str
    status: str
    validations_json: list[dict[str, object]]
    policy_evaluation_json: dict[str, object]
    evidence_refs: dict[str, object]
    expires_at: datetime
    version: int
    created_at: datetime
    updated_at: datetime


class ProposalEnvelope(BaseModel):
    data: ProposalRead
    meta: dict[str, str]


class ProposalCollection(BaseModel):
    data: list[ProposalRead]
    meta: dict[str, str | int]


class ProposalApprovalCreate(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    notes: str = Field(default="", max_length=1000)

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str) -> str:
        return value.strip()


class ProposalApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    proposal_id: UUID
    proposal_version: int
    approver_id: UUID
    decision: str
    notes: str
    decided_at: datetime


class ProposalApprovalEnvelope(BaseModel):
    data: ProposalApprovalRead
    meta: dict[str, str]


class DeploymentCreate(BaseModel):
    connector_type: str = Field(default="github", pattern="^(github|cms_staging|mock)$")
    current_live_content: str | None = None


class DeploymentReceiptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    proposal_id: UUID
    connector_type: str
    idempotency_key: str
    external_ref: str
    manifest_json: dict[str, object]
    status: str
    deployed_at: datetime
    verified_at: datetime | None = None


class DeploymentReceiptEnvelope(BaseModel):
    data: DeploymentReceiptRead
    meta: dict[str, str]


class PostDeployVerificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    proposal_id: UUID
    deployment_receipt_id: UUID
    page_id: UUID
    status: str
    verified_at: datetime | None
    expected_pattern: str
    observed_snippet: str | None
    http_status: int | None
    notes: str
    created_at: datetime


class PostDeployVerificationEnvelope(BaseModel):
    data: PostDeployVerificationRead
    meta: dict[str, str]


class MeasurementSeriesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    proposal_id: UUID
    page_id: UUID
    baseline_window_start: datetime
    baseline_window_end: datetime
    followup_window_start: datetime
    followup_window_end: datetime
    baseline_metrics: dict[str, object]
    followup_metrics: dict[str, object]
    delta_metrics: dict[str, object]
    confidence_score: float
    is_sparse: bool
    annotations: list[object]
    calculated_at: datetime


class MeasurementSeriesEnvelope(BaseModel):
    data: MeasurementSeriesRead
    meta: dict[str, str]


class MeasurementCollection(BaseModel):
    data: list[MeasurementSeriesRead]
    meta: dict[str, str | int]


class GovernanceSettingsUpdate(BaseModel):
    autopilot_enabled: bool | None = None
    daily_change_budget: int | None = Field(default=None, ge=1, le=50)
    freeze_window_start: datetime | None = None
    freeze_window_end: datetime | None = None


class GovernanceStatusRead(BaseModel):
    site_id: UUID
    mode: str
    autopilot_enabled: bool
    emergency_freeze: bool
    daily_change_budget: int
    today_deployments_count: int
    freeze_window_start: datetime | None
    freeze_window_end: datetime | None


class GovernanceStatusEnvelope(BaseModel):
    data: GovernanceStatusRead
    meta: dict[str, str]


class PolicySimulationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    evaluated_proposals_count: int
    auto_deployable_count: int
    review_required_count: int
    prohibited_count: int
    simulation_results_json: dict[str, object]
    run_at: datetime


class PolicySimulationEnvelope(BaseModel):
    data: PolicySimulationRead
    meta: dict[str, str]


class RollbackReceiptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    site_id: UUID
    proposal_id: UUID
    deployment_receipt_id: UUID
    restored_hash: str
    status: str
    rolled_back_at: datetime
    notes: str


class RollbackReceiptEnvelope(BaseModel):
    data: RollbackReceiptRead
    meta: dict[str, str]


