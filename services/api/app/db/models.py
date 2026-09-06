from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Site(Base):
    __tablename__ = "site"
    __table_args__ = (
        UniqueConstraint("tenant_id", "normalized_host"),
        Index("site_tenant_idx", "tenant_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200))
    canonical_origin: Mapped[str] = mapped_column(Text)
    normalized_host: Mapped[str] = mapped_column(String(253))
    mode: Mapped[str] = mapped_column(String(16), default="observe")
    status: Mapped[str] = mapped_column(String(32), default="pending_verification")
    autopilot_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency_freeze: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_change_budget: Mapped[int] = mapped_column(Integer, default=5)
    # None means "use the risk tier's default"; see evaluate_proposal_policy.
    required_approver_count: Mapped[int | None] = mapped_column(Integer)
    freeze_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freeze_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SiteVerificationChallenge(Base):
    __tablename__ = "site_verification_challenge"
    __table_args__ = (Index("verification_tenant_site_idx", "tenant_id", "site_id", "status"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(24), default="dns_txt")
    token_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CrawlJob(Base):
    __tablename__ = "crawl_job"
    __table_args__ = (
        Index("crawl_job_tenant_site_idx", "tenant_id", "site_id", "status"),
        Index(
            "crawl_job_one_active_per_site_idx",
            "tenant_id",
            "site_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="full")
    status: Mapped[str] = mapped_column(String(24), default="queued")
    requested_by: Mapped[UUID]
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Page(Base):
    __tablename__ = "page"
    __table_args__ = (
        UniqueConstraint("site_id", "url_hash"),
        Index("page_tenant_site_idx", "tenant_id", "site_id", "lifecycle_status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lifecycle_status: Mapped[str] = mapped_column(String(24), default="active")


class PageObservation(Base):
    __tablename__ = "page_observation"
    __table_args__ = (
        Index("observation_tenant_crawl_idx", "tenant_id", "crawl_job_id", "observed_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id"), nullable=False)
    crawl_job_id: Mapped[UUID] = mapped_column(ForeignKey("crawl_job.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    http_status: Mapped[int | None] = mapped_column(Integer)
    final_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    meta_description: Mapped[str | None] = mapped_column(Text)
    h1_json: Mapped[list[str]] = mapped_column(JSONB, default=list)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    rendered: Mapped[bool] = mapped_column(Boolean, default=False)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    robots_directives: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    structured_data_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    link_count_total: Mapped[int] = mapped_column(Integer, default=0)
    links_truncated: Mapped[bool] = mapped_column(Boolean, default=False)


class ScoringVersion(Base):
    __tablename__ = "scoring_version"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code_version: Mapped[str] = mapped_column(String(80), unique=True)
    factor_config_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    active_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisRun(Base):
    __tablename__ = "analysis_run"
    __table_args__ = (
        UniqueConstraint("crawl_job_id", "agent_type", "agent_version", "request_hash"),
        Index("analysis_run_tenant_site_idx", "tenant_id", "site_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    crawl_job_id: Mapped[UUID] = mapped_column(ForeignKey("crawl_job.id"), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(80), default="technical_rules")
    agent_version: Mapped[str] = mapped_column(String(80))
    evidence_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    finding_count: Mapped[int] = mapped_column(Integer, default=0)
    score_count: Mapped[int] = mapped_column(Integer, default=0)
    opportunity_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Finding(Base):
    __tablename__ = "finding"
    __table_args__ = (
        UniqueConstraint("tenant_id", "fingerprint"),
        Index("finding_tenant_site_status_idx", "tenant_id", "site_id", "status", "severity"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id"), nullable=False)
    analysis_run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    scoring_version_id: Mapped[UUID] = mapped_column(ForeignKey("scoring_version.id"), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(40), default="technical")
    evidence_refs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(Text)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="open")
    fingerprint: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PageScore(Base):
    __tablename__ = "page_score"
    __table_args__ = (
        UniqueConstraint("page_id", "scoring_version_id", "observation_id"),
        Index("page_score_tenant_page_idx", "tenant_id", "page_id", "calculated_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id"), nullable=False)
    observation_id: Mapped[UUID] = mapped_column(ForeignKey("page_observation.id"), nullable=False)
    analysis_run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    scoring_version_id: Mapped[UUID] = mapped_column(ForeignKey("scoring_version.id"), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    score: Mapped[int] = mapped_column(Integer)
    factors_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Opportunity(Base):
    __tablename__ = "opportunity"
    __table_args__ = (
        UniqueConstraint("tenant_id", "fingerprint"),
        Index("opportunity_top20_idx", "tenant_id", "site_id", "status", "score", "fingerprint"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(40), default="technical")
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open")
    impact: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    urgency: Mapped[float] = mapped_column(Float)
    effort: Mapped[float] = mapped_column(Float)
    risk: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float)
    scoring_version_id: Mapped[UUID] = mapped_column(ForeignKey("scoring_version.id"), nullable=False)
    evidence_refs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64))
    suppressed_reason: Mapped[str | None] = mapped_column(Text)
    suppressed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suppressed_by: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpportunityFinding(Base):
    __tablename__ = "opportunity_finding"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), primary_key=True)
    opportunity_id: Mapped[UUID] = mapped_column(ForeignKey("opportunity.id"), primary_key=True)
    finding_id: Mapped[UUID] = mapped_column(ForeignKey("finding.id"), primary_key=True)


class CalibrationRun(Base):
    __tablename__ = "calibration_run"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "idempotency_key"),
        Index("calibration_run_tenant_site_idx", "tenant_id", "site_id", "status", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open")
    strategy: Mapped[str] = mapped_column(String(40), default="top_opportunities_v1")
    target_size: Mapped[int] = mapped_column(Integer)
    scoring_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("scoring_version.id"))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CalibrationItem(Base):
    __tablename__ = "calibration_item"
    __table_args__ = (
        UniqueConstraint("calibration_run_id", "ordinal"),
        UniqueConstraint("calibration_run_id", "opportunity_id"),
        Index("calibration_item_tenant_run_idx", "tenant_id", "calibration_run_id", "ordinal"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    calibration_run_id: Mapped[UUID] = mapped_column(ForeignKey("calibration_run.id"))
    opportunity_id: Mapped[UUID] = mapped_column(ForeignKey("opportunity.id"))
    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    rule_key: Mapped[str] = mapped_column(String(120))
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CalibrationReview(Base):
    __tablename__ = "calibration_review"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reviewer_id", "idempotency_key"),
        Index(
            "calibration_review_tenant_item_idx",
            "tenant_id",
            "calibration_item_id",
            "reviewer_id",
            "created_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    calibration_item_id: Mapped[UUID] = mapped_column(ForeignKey("calibration_item.id"))
    reviewer_id: Mapped[UUID]
    accuracy_label: Mapped[str] = mapped_column(String(24))
    actionability: Mapped[str] = mapped_column(String(16))
    severity_fit: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str] = mapped_column(Text, default="")
    request_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Connector(Base):
    __tablename__ = "connector"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "type"),
        Index("connector_tenant_site_idx", "tenant_id", "site_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(48))
    provider_key: Mapped[str | None] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(32), default="pending_authorization")
    external_account_ref: Mapped[str | None] = mapped_column(Text)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    # Non-secret settings this connector needs to be usable: for GitHub, the
    # base branch and the URL-path-to-file mapping. Kept out of the encrypted
    # payload so reading them never requires decrypting a credential.
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    granted_scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    consented_by: Mapped[UUID | None]
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectorOauthState(Base):
    __tablename__ = "connector_oauth_state"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    connector_id: Mapped[UUID] = mapped_column(ForeignKey("connector.id"), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    pkce_verifier_ref: Mapped[str | None] = mapped_column(Text)
    requested_scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    requested_property_ref: Mapped[str | None] = mapped_column(Text)
    redirect_path: Mapped[str] = mapped_column(Text, default="/settings/connectors")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectorSecret(Base):
    __tablename__ = "connector_secret"
    __table_args__ = (Index("connector_secret_active_idx", "tenant_id", "connector_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    connector_id: Mapped[UUID] = mapped_column(ForeignKey("connector.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(48))
    ciphertext: Mapped[bytes]
    nonce: Mapped[bytes]
    aad_hash: Mapped[str] = mapped_column(String(64))
    key_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectorSync(Base):
    __tablename__ = "connector_sync"
    __table_args__ = (
        UniqueConstraint("tenant_id", "connector_id", "idempotency_key"),
        Index("connector_sync_tenant_idx", "tenant_id", "connector_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    connector_id: Mapped[UUID] = mapped_column(ForeignKey("connector.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    cursor_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    range_start: Mapped[date] = mapped_column(Date)
    range_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    counts_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    requested_by: Mapped[UUID]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SearchMetric(Base):
    __tablename__ = "search_metric"
    __table_args__ = (
        Index("search_metric_site_date_idx", "tenant_id", "site_id", "metric_date"),
        UniqueConstraint(
            "tenant_id",
            "site_id",
            "metric_date",
            "query_hash",
            "page_url_hash",
            "country",
            "device",
            "search_type",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    page_id: Mapped[UUID | None] = mapped_column(ForeignKey("page.id"))
    metric_date: Mapped[date] = mapped_column(Date)
    query_hash: Mapped[str] = mapped_column(String(64))
    page_url: Mapped[str] = mapped_column(Text)
    page_url_hash: Mapped[str] = mapped_column(String(64))
    country: Mapped[str] = mapped_column(String(8), default="")
    device: Mapped[str] = mapped_column(String(24), default="")
    search_type: Mapped[str] = mapped_column(String(24), default="web")
    clicks: Mapped[float] = mapped_column(Float)
    impressions: Mapped[float] = mapped_column(Float)
    ctr: Mapped[float] = mapped_column(Float)
    position: Mapped[float] = mapped_column(Float)
    source_sync_id: Mapped[UUID] = mapped_column(ForeignKey("connector_sync.id"))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PerformanceRun(Base):
    __tablename__ = "performance_run"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "idempotency_key"),
        Index("performance_run_tenant_site_idx", "tenant_id", "site_id", "created_at"),
        Index(
            "performance_run_one_active_idx",
            "tenant_id",
            "site_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id"), nullable=False)
    crawl_job_id: Mapped[UUID] = mapped_column(ForeignKey("crawl_job.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    strategy: Mapped[str] = mapped_column(String(16), default="mobile")
    source: Mapped[str] = mapped_column(String(32), default="pagespeed_insights")
    target_url: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[UUID]
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PerformanceObservation(Base):
    __tablename__ = "performance_observation"
    __table_args__ = (
        UniqueConstraint("performance_run_id"),
        Index("performance_observation_tenant_site_idx", "tenant_id", "site_id", "observed_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    performance_run_id: Mapped[UUID] = mapped_column(ForeignKey("performance_run.id"))
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    strategy: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(32))
    lighthouse_version: Mapped[str] = mapped_column(String(80))
    performance_score: Mapped[int] = mapped_column(Integer)
    lcp_ms: Mapped[float | None] = mapped_column(Float)
    inp_ms: Mapped[float | None] = mapped_column(Float)
    cls: Mapped[float | None] = mapped_column(Float)
    ttfb_ms: Mapped[float | None] = mapped_column(Float)


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (Index("audit_event_tenant_time_idx", "tenant_id", "occurred_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor_type: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(120))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))


class OutboxEvent(Base):
    __tablename__ = "outbox_event"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120))
    event_version: Mapped[int] = mapped_column(Integer)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[UUID]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class Proposal(Base):
    __tablename__ = "proposal"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        Index("proposal_tenant_site_status_idx", "tenant_id", "site_id", "status", "created_at"),
        Index("proposal_tenant_opportunity_idx", "tenant_id", "opportunity_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    opportunity_id: Mapped[UUID] = mapped_column(ForeignKey("opportunity.id"), nullable=False)
    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id"), nullable=False)
    author_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(String(240))
    rationale: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(String(40))
    target_path: Mapped[str] = mapped_column(String(1024))
    before_content: Mapped[str] = mapped_column(Text)
    after_content: Mapped[str] = mapped_column(Text)
    diff_unified: Mapped[str] = mapped_column(Text)
    base_hash: Mapped[str] = mapped_column(String(64))
    proposal_hash: Mapped[str] = mapped_column(String(64))
    risk: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="draft")
    validations_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    policy_evaluation_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    evidence_refs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProposalApproval(Base):
    __tablename__ = "proposal_approval"
    __table_args__ = (
        UniqueConstraint("tenant_id", "proposal_id", "proposal_version", "approver_id"),
        Index("proposal_approval_tenant_proposal_idx", "tenant_id", "proposal_id", "decided_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("proposal.id"), nullable=False)
    proposal_version: Mapped[int] = mapped_column(Integer)
    approver_id: Mapped[UUID]
    decision: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeploymentReceipt(Base):
    __tablename__ = "deployment_receipt"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        UniqueConstraint("id", "tenant_id"),
        Index("deployment_receipt_tenant_site_idx", "tenant_id", "site_id", "deployed_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("proposal.id"), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    external_ref: Mapped[str] = mapped_column(Text)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default="applied")
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostDeployVerification(Base):
    __tablename__ = "post_deploy_verification"
    __table_args__ = (
        UniqueConstraint("tenant_id", "deployment_receipt_id"),
        UniqueConstraint("id", "tenant_id"),
        Index("verification_tenant_site_idx", "tenant_id", "site_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("proposal.id"), nullable=False)
    deployment_receipt_id: Mapped[UUID] = mapped_column(ForeignKey("deployment_receipt.id"), nullable=False)
    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_pattern: Mapped[str] = mapped_column(Text)
    observed_snippet: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MeasurementSeries(Base):
    __tablename__ = "measurement_series"
    __table_args__ = (
        UniqueConstraint("tenant_id", "proposal_id", "followup_window_end"),
        UniqueConstraint("id", "tenant_id"),
        Index("measurement_tenant_site_idx", "tenant_id", "site_id", "calculated_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("proposal.id"), nullable=False)
    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id"), nullable=False)
    baseline_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    baseline_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    followup_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    followup_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    baseline_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    followup_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    delta_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confidence_score: Mapped[float] = mapped_column(Float)
    is_sparse: Mapped[bool] = mapped_column(Boolean, default=False)
    annotations: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicySimulationRun(Base):
    __tablename__ = "policy_simulation_run"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        Index("simulation_tenant_site_idx", "tenant_id", "site_id", "run_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    evaluated_proposals_count: Mapped[int] = mapped_column(Integer)
    auto_deployable_count: Mapped[int] = mapped_column(Integer)
    review_required_count: Mapped[int] = mapped_column(Integer)
    prohibited_count: Mapped[int] = mapped_column(Integer)
    simulation_results_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RollbackReceipt(Base):
    __tablename__ = "rollback_receipt"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        Index("rollback_tenant_site_idx", "tenant_id", "site_id", "rolled_back_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("proposal.id"), nullable=False)
    deployment_receipt_id: Mapped[UUID] = mapped_column(ForeignKey("deployment_receipt.id"), nullable=False)
    restored_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="applied")
    rolled_back_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notes: Mapped[str] = mapped_column(Text, default="")
    external_ref: Mapped[str] = mapped_column(Text, default="")


class Routine(Base):
    __tablename__ = "routine"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        UniqueConstraint("tenant_id", "site_id", "kind"),
        Index("routine_tenant_site_idx", "tenant_id", "site_id", "kind"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32))
    cadence: Mapped[str] = mapped_column(String(16))
    schedule_hour_utc: Mapped[int] = mapped_column(SmallInteger, default=6)
    schedule_minute_utc: Mapped[int] = mapped_column(SmallInteger, default=0)
    schedule_isodow: Mapped[int | None] = mapped_column(SmallInteger)
    schedule_dom: Mapped[int | None] = mapped_column(SmallInteger)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RoutineRun(Base):
    __tablename__ = "routine_run"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        UniqueConstraint("routine_id", "scheduled_for"),
        Index("routine_run_tenant_site_idx", "tenant_id", "site_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    routine_id: Mapped[UUID] = mapped_column(nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    skip_reason: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Report(Base):
    __tablename__ = "report"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        UniqueConstraint("tenant_id", "site_id", "kind", "period_start", "period_end"),
        Index("report_tenant_site_idx", "tenant_id", "site_id", "generated_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    routine_run_id: Mapped[UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(String(32))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    scoring_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("scoring_version.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationChannel(Base):
    __tablename__ = "notification_channel"
    __table_args__ = (UniqueConstraint("id", "tenant_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID | None] = mapped_column(ForeignKey("site.id"))
    kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    destination_hint: Mapped[str] = mapped_column(String(200))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    aad_hash: Mapped[str] = mapped_column(String(64))
    key_version: Mapped[str] = mapped_column(String(80))
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(Base):
    __tablename__ = "notification_delivery"
    __table_args__ = (UniqueConstraint("channel_id", "report_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    report_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SearchQuery(Base):
    __tablename__ = "search_query"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), primary_key=True)
    site_id: Mapped[UUID] = mapped_column(primary_key=True)
    query_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    aad_hash: Mapped[str] = mapped_column(String(64))
    key_version: Mapped[str] = mapped_column(String(80))
    term_length: Mapped[int] = mapped_column(SmallInteger)
    token_count: Mapped[int] = mapped_column(SmallInteger)
    is_question: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KeywordAnalysisRun(Base):
    __tablename__ = "keyword_analysis_run"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        UniqueConstraint(
            "tenant_id", "site_id", "window_start", "window_end", "algorithm_version"
        ),
        Index("keyword_analysis_run_tenant_site_idx", "tenant_id", "site_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    routine_run_id: Mapped[UUID | None] = mapped_column()
    algorithm_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="completed")
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    queries_considered: Mapped[int] = mapped_column(Integer, default=0)
    clusters_built: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KeywordCluster(Base):
    __tablename__ = "keyword_cluster"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        UniqueConstraint("analysis_run_id", "cluster_key"),
        Index(
            "keyword_cluster_ranked_idx",
            "tenant_id",
            "site_id",
            "analysis_run_id",
            "opportunity_score",
            "cluster_key",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    analysis_run_id: Mapped[UUID] = mapped_column(nullable=False)
    label: Mapped[str] = mapped_column(String(200))
    cluster_key: Mapped[str] = mapped_column(String(200))
    intent: Mapped[str] = mapped_column(String(20))
    answer_engine_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    member_count: Mapped[int] = mapped_column(Integer)
    clicks: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    best_position: Mapped[float | None] = mapped_column(Float)
    average_position: Mapped[float | None] = mapped_column(Float)
    striking_distance_count: Mapped[int] = mapped_column(Integer, default=0)
    primary_page_id: Mapped[UUID | None] = mapped_column()
    competing_page_count: Mapped[int] = mapped_column(Integer, default=0)
    opportunity_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KeywordClusterMember(Base):
    __tablename__ = "keyword_cluster_member"
    __table_args__ = (
        Index("keyword_cluster_member_cluster_idx", "tenant_id", "cluster_id", "impressions"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), primary_key=True)
    cluster_id: Mapped[UUID] = mapped_column(primary_key=True)
    query_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    site_id: Mapped[UUID] = mapped_column(nullable=False)
    clicks: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    position: Mapped[float] = mapped_column(Float, default=0.0)
    best_page_id: Mapped[UUID | None] = mapped_column()


class ContentBrief(Base):
    __tablename__ = "content_brief"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        UniqueConstraint("tenant_id", "keyword_cluster_id"),
        Index("content_brief_queue_idx", "tenant_id", "site_id", "status", "priority_score", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    keyword_cluster_id: Mapped[UUID] = mapped_column(nullable=False)
    analysis_run_id: Mapped[UUID] = mapped_column(nullable=False)
    routine_run_id: Mapped[UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    target_page_id: Mapped[UUID | None] = mapped_column()
    cluster_label: Mapped[str] = mapped_column(String(200))
    intent: Mapped[str] = mapped_column(String(20))
    answer_engine_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    priority_score: Mapped[float] = mapped_column(Float)
    sections_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    query_hashes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    content_hash: Mapped[str] = mapped_column(String(64))
    dismissed_reason: Mapped[str | None] = mapped_column(String(200))
    dismissed_by: Mapped[UUID | None] = mapped_column()
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Competitor(Base):
    __tablename__ = "competitor"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        UniqueConstraint("tenant_id", "site_id", "normalized_host"),
        Index("competitor_tenant_site_idx", "tenant_id", "site_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    normalized_host: Mapped[str] = mapped_column(String(253))
    label: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CompetitorPage(Base):
    __tablename__ = "competitor_page"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        UniqueConstraint("tenant_id", "competitor_id", "url_hash"),
        Index("competitor_page_tenant_site_idx", "tenant_id", "site_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    competitor_id: Mapped[UUID] = mapped_column(nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64))
    keyword_cluster_key: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompetitorScan(Base):
    __tablename__ = "competitor_scan"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        Index("competitor_scan_tenant_site_idx", "tenant_id", "site_id", "started_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    routine_run_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pages_requested: Mapped[int] = mapped_column(Integer, default=0)
    pages_observed: Mapped[int] = mapped_column(Integer, default=0)
    pages_blocked: Mapped[int] = mapped_column(Integer, default=0)
    pages_failed: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))


class CompetitorObservation(Base):
    __tablename__ = "competitor_observation"
    __table_args__ = (
        UniqueConstraint("competitor_scan_id", "competitor_page_id"),
        Index("competitor_observation_page_idx", "tenant_id", "competitor_page_id", "observed_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    competitor_scan_id: Mapped[UUID] = mapped_column(nullable=False)
    competitor_page_id: Mapped[UUID] = mapped_column(nullable=False)
    site_id: Mapped[UUID] = mapped_column(nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    outcome: Mapped[str] = mapped_column(String(24))
    http_status: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    meta_description: Mapped[str | None] = mapped_column(Text)
    h1_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    heading_count: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    internal_link_count: Mapped[int] = mapped_column(Integer, default=0)
    structured_data_types: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    content_hash: Mapped[str | None] = mapped_column(String(64))


class AiVisibilitySnapshot(Base):
    __tablename__ = "ai_visibility_snapshot"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        UniqueConstraint("tenant_id", "site_id", "captured_on"),
        Index("ai_visibility_snapshot_tenant_site_idx", "tenant_id", "site_id", "captured_on"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    routine_run_id: Mapped[UUID | None] = mapped_column()
    crawl_job_id: Mapped[UUID | None] = mapped_column()
    captured_on: Mapped[date] = mapped_column(Date)
    readiness_score: Mapped[float] = mapped_column(Float)
    factors_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    citation_source: Mapped[str] = mapped_column(String(16), default="none")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentSession(Base):
    __tablename__ = "agent_session"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        Index("agent_session_tenant_site_idx", "tenant_id", "site_id", "updated_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="active")
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentTask(Base):
    __tablename__ = "agent_task"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        Index("agent_task_tenant_site_idx", "tenant_id", "site_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    session_id: Mapped[UUID | None] = mapped_column()
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    skill_key: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    routine_run_id: Mapped[UUID | None] = mapped_column()
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    requested_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentMessage(Base):
    __tablename__ = "agent_message"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence"),
        Index("agent_message_session_idx", "tenant_id", "session_id", "sequence"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    session_id: Mapped[UUID] = mapped_column(nullable=False)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("site.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(8))
    body: Mapped[str] = mapped_column(Text)
    skill_key: Mapped[str | None] = mapped_column(String(60))
    agent_task_id: Mapped[UUID | None] = mapped_column()
    evidence_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
