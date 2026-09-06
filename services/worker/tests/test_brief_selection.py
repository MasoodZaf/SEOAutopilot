"""Which clusters earn a brief on a site that has not started ranking.

`MIN_CLUSTER_SCORE` is absolute, and the score it reads is dominated by absolute
demand. That makes it unreachable for the sites the briefs would help most: the
pilot site's strongest topic scored 12.5 from 41 impressions at position 86, so
`content_briefs` skipped with `no_cluster_met_threshold` and the only mechanism
in the product that says "here is what to add to this page" had never produced
anything at all.

Offering the site's own strongest topics instead is only defensible if nobody
can mistake them for topics that cleared the bar, so what is pinned here is as
much the labelling as the selection.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from app.briefs.generate import (
    EXPLORATORY_FLOOR,
    MAX_BRIEFS_PER_RUN,
    MAX_EXPLORATORY_BRIEFS,
    MIN_CLUSTER_SCORE,
    generate_briefs,
)

TENANT = UUID("019d0000-0000-7000-8000-000000000011")
SITE = UUID("019d0000-0000-7000-8000-000000000012")
RUN = UUID("019d0000-0000-7000-8000-000000000013")


def cluster(score: float, label: str = "calculator debt") -> dict[str, Any]:
    return {
        "id": uuid4(),
        "analysis_run_id": RUN,
        "label": label,
        "intent": "informational",
        "answer_engine_candidate": False,
        "impressions": 41.0,
        "clicks": 0.0,
        "ctr": 0.0,
        "average_position": 85.7,
        "striking_distance_count": 0,
        "competing_page_count": 1,
        "opportunity_score": score,
        "member_count": 20,
        "primary_page_id": None,
        "query_hashes": ["a" * 64],
    }


class FakeConnection:
    """Applies the score bound the caller passes, and records every write."""

    def __init__(self, clusters: list[dict[str, Any]]) -> None:
        self.clusters = clusters
        self.fetches: list[tuple[float, int]] = []
        self.written: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        assert "FROM keyword_cluster" in query
        minimum, limit = float(args[3]), int(args[4])
        self.fetches.append((minimum, limit))
        eligible = sorted(
            (row for row in self.clusters if row["opportunity_score"] >= minimum),
            key=lambda row: -row["opportunity_score"],
        )
        return eligible[:limit]

    async def fetchrow(self, query: str, *args: Any) -> Any:
        return None

    async def execute(self, query: str, *args: Any) -> str:
        self.written.append((query, args))
        return "OK"

    def briefs(self) -> list[tuple[Any, ...]]:
        return [args for query, args in self.written if "INSERT INTO content_brief" in query]


async def generate(connection: FakeConnection) -> dict[str, Any]:
    return await generate_briefs(connection, TENANT, SITE, RUN)


@pytest.mark.asyncio
async def test_a_site_with_real_demand_is_unaffected() -> None:
    """The strong case must not change; it was never the broken one."""
    connection = FakeConnection([cluster(41.0), cluster(22.0, "calculator loan")])
    summary = await generate(connection)

    assert summary["selection_basis"] == "demand"
    assert summary["briefs_written"] == 2
    # One query, at the real bar. No fallback was reached for.
    assert connection.fetches == [(MIN_CLUSTER_SCORE, MAX_BRIEFS_PER_RUN)]


@pytest.mark.asyncio
async def test_a_site_below_the_bar_gets_its_strongest_topics_anyway() -> None:
    """The pilot site's actual numbers.

    Nothing here clears 20. Before this, that produced zero briefs and a skip,
    on a site whose single biggest search topic was 41 impressions across 20
    queries -- the most useful thing the product knew about it.
    """
    connection = FakeConnection(
        [cluster(12.5), cluster(12.23, "calchive"), cluster(5.45, "calculator sleep")]
    )
    summary = await generate(connection)

    assert summary["selection_basis"] == "exploratory"
    assert summary["briefs_written"] == 3
    assert connection.fetches == [
        (MIN_CLUSTER_SCORE, MAX_BRIEFS_PER_RUN),
        (EXPLORATORY_FLOOR, MAX_EXPLORATORY_BRIEFS),
    ]


@pytest.mark.asyncio
async def test_every_exploratory_brief_says_so_in_its_own_evidence() -> None:
    """A run summary is not where somebody reads a brief months later.

    Presenting "the best this site has" identically to "this cleared the bar"
    would be the product overstating what it knows, which is the one thing it
    cannot afford to do.
    """
    connection = FakeConnection([cluster(12.5)])
    await generate(connection)

    written = connection.briefs()
    assert len(written) == 1
    # evidence_json is the 13th bound parameter.
    assert '"selection_basis":"exploratory"' in written[0][12]


@pytest.mark.asyncio
async def test_a_brief_that_cleared_the_bar_is_labelled_on_merit() -> None:
    connection = FakeConnection([cluster(41.0)])
    await generate(connection)

    assert '"selection_basis":"demand"' in connection.briefs()[0][12]


@pytest.mark.asyncio
async def test_the_fallback_is_a_short_list_not_a_firehose() -> None:
    """The reason the bar existed: more work than a team can read.

    Relaxing it for a quiet site must not turn the long tail into fifty briefs
    nobody will open.
    """
    connection = FakeConnection([cluster(10.0 - n * 0.1) for n in range(40)])
    summary = await generate(connection)

    assert summary["briefs_written"] == MAX_EXPLORATORY_BRIEFS
    assert MAX_EXPLORATORY_BRIEFS < MAX_BRIEFS_PER_RUN


@pytest.mark.asyncio
async def test_a_single_stray_impression_is_still_noise() -> None:
    """A floor, not an absence of one.

    Every cluster here is one or two impressions on one query. Writing briefs
    for those would be inventing work from noise.
    """
    connection = FakeConnection([cluster(2.9), cluster(1.1, "checker date")])
    summary = await generate(connection)

    assert summary["briefs_written"] == 0
    assert connection.briefs() == []


@pytest.mark.asyncio
async def test_a_site_with_no_clusters_writes_nothing() -> None:
    connection = FakeConnection([])
    summary = await generate(connection)

    assert summary["briefs_written"] == 0
    assert summary["clusters_considered"] == 0
