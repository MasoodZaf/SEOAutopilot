from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.schemas import OpportunitySuppress
from app.core.context import Role, TenantContext
from app.db.models import Opportunity
from app.services.opportunities import (
    OpportunityService,
    build_diverse_top_query,
    build_latest_analyzed_crawl_query,
    build_not_rechecked_count_query,
    build_page_url_query,
)


def test_diverse_top_query_is_deterministic_and_tenant_scoped() -> None:
    statement = build_diverse_top_query(
        tenant_id=uuid4(),
        site_id=uuid4(),
        opportunity_status="open",
        limit=20,
        opportunity_type="technical",
        min_score=50.0,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "row_number() OVER (PARTITION BY opportunity.type, opportunity.title" in sql
    assert sql.count("opportunity.tenant_id =") == 2
    assert sql.count("opportunity.site_id =") == 2
    assert "opportunity.suppressed_reason IS NULL" in sql
    assert "opportunity.risk !=" in sql
    assert "opportunity.type =" in sql
    assert "opportunity.score >=" in sql
    assert "ORDER BY anon_1.diversity_rank" in sql


def test_diverse_top_query_can_be_limited_to_one_crawls_evidence() -> None:
    crawl_id = uuid4()
    statement = build_diverse_top_query(
        tenant_id=uuid4(),
        site_id=uuid4(),
        opportunity_status="open",
        limit=20,
        evidence_crawl_id=crawl_id,
    )

    compiled = statement.compile(dialect=postgresql.dialect())

    assert "opportunity.evidence_refs ->> " in str(compiled)
    assert str(crawl_id) in compiled.params.values()


def test_diverse_top_query_has_no_crawl_filter_by_default() -> None:
    statement = build_diverse_top_query(
        tenant_id=uuid4(), site_id=uuid4(), opportunity_status="open", limit=20
    )

    assert "evidence_refs ->>" not in str(statement.compile(dialect=postgresql.dialect()))


def test_latest_analyzed_crawl_query_reads_completed_runs_of_one_site() -> None:
    sql = str(
        build_latest_analyzed_crawl_query(tenant_id=uuid4(), site_id=uuid4()).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "analysis_run.tenant_id =" in sql
    assert "analysis_run.site_id =" in sql
    assert "analysis_run.status =" in sql
    assert "ORDER BY analysis_run.created_at DESC" in sql


def test_not_rechecked_count_counts_open_items_from_other_crawls() -> None:
    sql = str(
        build_not_rechecked_count_query(
            tenant_id=uuid4(), site_id=uuid4(), evidence_crawl_id=uuid4()
        ).compile(dialect=postgresql.dialect())
    )

    assert "count(opportunity.id)" in sql
    assert "opportunity.tenant_id =" in sql
    assert "opportunity.status =" in sql
    assert "opportunity.suppressed_reason IS NULL" in sql
    # A row with no crawl in its evidence counts as not re-checked, not as current.
    assert "coalesce((opportunity.evidence_refs ->> " in sql


def test_page_url_query_is_tenant_and_site_scoped() -> None:
    statement = build_page_url_query(
        tenant_id=uuid4(),
        site_id=uuid4(),
        page_ids=[uuid4(), uuid4()],
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "page.tenant_id =" in sql
    assert "page.site_id =" in sql
    assert "page.id IN" in sql


@pytest.mark.asyncio
async def test_suppress_and_unsuppress_opportunity_lifecycle() -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    site_id = uuid4()
    opp_id = uuid4()

    context = TenantContext(tenant_id=tenant_id, actor_id=actor_id, role=Role.SEO_MANAGER, trace_id="tr-123")
    mock_opp = Opportunity(
        id=opp_id,
        tenant_id=tenant_id,
        site_id=site_id,
        page_id=uuid4(),
        type="technical",
        title="Missing Title Tag",
        status="open",
        impact=0.8,
        confidence=0.9,
        urgency=0.7,
        effort=0.2,
        risk="low",
        score=75.0,
        scoring_version_id=uuid4(),
        evidence_refs={"page_id": str(uuid4())},
        fingerprint="a" * 64,
    )

    session = AsyncMock()
    session.add = MagicMock()
    service = OpportunityService(session, context)
    service.get = AsyncMock(return_value=mock_opp)  # type: ignore[method-assign]

    # Suppress
    command = OpportunitySuppress(reason="intentional_design", notes="Handled in template")
    suppressed = await service.suppress(opp_id, command)

    assert suppressed.status == "suppressed"
    assert suppressed.suppressed_reason == "intentional_design"
    assert suppressed.suppressed_by == actor_id
    assert session.add.call_count == 2  # AuditEvent and OutboxEvent
    assert session.flush.called

    # Unsuppress
    session.add.reset_mock()
    session.flush.reset_mock()

    unsuppressed = await service.unsuppress(opp_id)
    assert unsuppressed.status == "open"
    assert unsuppressed.suppressed_reason is None
    assert unsuppressed.suppressed_by is None
    assert session.add.call_count == 2
    assert session.flush.called


@pytest.mark.asyncio
async def test_suppress_opportunity_forbidden_for_viewer() -> None:
    context = TenantContext(tenant_id=uuid4(), actor_id=uuid4(), role=Role.VIEWER, trace_id="tr-123")
    session = AsyncMock()
    service = OpportunityService(session, context)

    with pytest.raises(HTTPException) as exc:
        await service.suppress(uuid4(), OpportunitySuppress(reason="duplicate"))
    assert exc.value.status_code == 403
