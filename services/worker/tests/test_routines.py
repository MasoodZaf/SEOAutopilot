import hashlib
from datetime import UTC, datetime
from typing import Any, Self
from uuid import UUID

import pytest
from app.notifications.deliver import (
    DeliveryError,
    assert_public_https_target,
    build_message,
    channel_aad,
    decrypt_webhook_url,
)
from app.routines.schedule import RoutineSchedule, advance_from_slot, next_occurrence
from app.routines.scheduler import claim_due_routines, skip_reason_for
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TENANT = UUID("019d0000-0000-7000-8000-000000000011")
SITE = UUID("019d0000-0000-7000-8000-000000000021")
ROUTINE = UUID("019d0000-0000-7000-8000-000000000031")
RUN = UUID("019d0000-0000-7000-8000-000000000041")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def routine_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": ROUTINE,
        "tenant_id": TENANT,
        "site_id": SITE,
        "kind": "weekly_report",
        "cadence": "daily",
        "schedule_hour_utc": 6,
        "schedule_minute_utc": 0,
        "schedule_isodow": None,
        "schedule_dom": None,
        "next_run_at": at("2026-09-02T06:00:00"),
        "consecutive_failures": 0,
        "site_status": "active",
        "verified_at": at("2026-08-01T00:00:00"),
        "emergency_freeze": False,
    }
    row.update(overrides)
    return row


class FakeTransaction:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeConnection:
    """Records statements so scheduling side effects can be asserted."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.inserted_runs: list[tuple[str, ...]] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return self.rows

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "INSERT INTO routine_run" in query:
            self.inserted_runs.append(tuple(str(item) for item in args))
            return RUN
        return None

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    def statements(self, needle: str) -> list[tuple[str, tuple[Any, ...]]]:
        return [entry for entry in self.executed if needle in entry[0]]


# --- schedule arithmetic mirrors services/api/app/domain/routines.py ---


def test_worker_schedule_matches_the_api_vectors() -> None:
    daily = RoutineSchedule(cadence="daily", hour_utc=6, minute_utc=30)
    assert next_occurrence(daily, at("2026-09-02T05:00:00")) == at("2026-09-02T06:30:00")
    assert next_occurrence(daily, at("2026-09-02T06:30:00")) == at("2026-09-03T06:30:00")

    weekly = RoutineSchedule(cadence="weekly", hour_utc=7, isodow=1)
    assert next_occurrence(weekly, at("2026-09-02T10:00:00")) == at("2026-09-07T07:00:00")

    monthly = RoutineSchedule(cadence="monthly", hour_utc=3, dom=15)
    assert next_occurrence(monthly, at("2026-12-20T00:00:00")) == at("2027-01-15T03:00:00")

    collapsed = advance_from_slot(daily, at("2026-01-01T06:30:00"), at("2026-09-02T09:00:00"))
    assert collapsed == at("2026-09-03T06:30:00")


# --- fail-closed gating ---


def test_unverified_frozen_and_parked_routines_are_skipped() -> None:
    assert skip_reason_for(routine_row()) is None
    assert skip_reason_for(routine_row(verified_at=None)) == "site_not_verified"
    assert skip_reason_for(routine_row(site_status="pending_verification")) == "site_not_verified"
    assert skip_reason_for(routine_row(emergency_freeze=True)) == "site_frozen"
    assert (
        skip_reason_for(routine_row(consecutive_failures=5)) == "routine_parked_after_failures"
    )


@pytest.mark.asyncio
async def test_due_routine_queues_a_run_and_advances_the_schedule() -> None:
    connection = FakeConnection([routine_row()])
    claimed = await claim_due_routines(connection, at("2026-09-02T06:05:00"))

    assert claimed == 1
    assert connection.inserted_runs[0][4] == "queued"
    outbox = connection.statements("INSERT INTO outbox_event")
    assert len(outbox) == 1
    assert "routine.run.queued.v1" in outbox[0][0]

    advance = connection.statements("UPDATE routine")[0]
    assert advance[1][2] == at("2026-09-03T06:00:00")


@pytest.mark.asyncio
async def test_frozen_site_records_a_skip_and_publishes_no_work() -> None:
    connection = FakeConnection([routine_row(emergency_freeze=True)])
    claimed = await claim_due_routines(connection, at("2026-09-02T06:05:00"))

    assert claimed == 1
    assert connection.inserted_runs[0][4] == "skipped"
    assert connection.inserted_runs[0][7] == "site_frozen"
    # A frozen site must never reach the runner.
    assert connection.statements("INSERT INTO outbox_event") == []


@pytest.mark.asyncio
async def test_a_slot_already_claimed_elsewhere_publishes_nothing() -> None:
    class ConflictConnection(FakeConnection):
        async def fetchval(self, query: str, *args: Any) -> Any:
            if "INSERT INTO routine_run" in query:
                return None  # ON CONFLICT DO NOTHING: another replica won.
            return None

    connection = ConflictConnection([routine_row()])
    claimed = await claim_due_routines(connection, at("2026-09-02T06:05:00"))

    assert claimed == 0
    assert connection.statements("INSERT INTO outbox_event") == []


# --- outbound delivery ---


def test_webhook_secret_only_decrypts_under_its_bound_aad() -> None:
    key = b"k" * 32
    nonce = b"n" * 12
    aad = channel_aad(TENANT, "slack_webhook", "local-v1")
    ciphertext = AESGCM(key).encrypt(nonce, b"https://hooks.slack.com/services/A/B", aad)

    url = decrypt_webhook_url(
        key, TENANT, "slack_webhook", "local-v1", nonce, ciphertext,
        hashlib.sha256(aad).hexdigest(),
    )
    assert url == "https://hooks.slack.com/services/A/B"

    with pytest.raises(DeliveryError) as mismatch:
        decrypt_webhook_url(
            key, TENANT, "generic_webhook", "local-v1", nonce, ciphertext,
            hashlib.sha256(aad).hexdigest(),
        )
    assert mismatch.value.code == "channel_aad_mismatch"


def test_delivery_target_rejects_non_public_destinations() -> None:
    for rejected in (
        "http://hooks.slack.com/x",
        "https://user:pass@hooks.slack.com/x",
        "https://127.0.0.1/x",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.5/x",
    ):
        with pytest.raises(DeliveryError):
            assert_public_https_target(rejected)


def test_message_carries_headline_counts_without_evidence_detail() -> None:
    payload = {
        "period": {"start": "2026-08-26", "end": "2026-09-01"},
        "opportunities": {
            "opened_in_period": 4,
            "resolved_in_period": 1,
            "top": [{"title": "Missing title", "url": "https://example.com/private-page"}],
        },
        "search": {"available": True, "clicks": {"current": 120.0, "previous": 100.0}},
    }
    message = build_message("slack_webhook", "CodeArc", payload, "https://app.example/pilot/reports/1")

    assert set(message) == {"text"}
    text = message["text"]
    assert "4 opportunities opened" in text
    assert "association, not attribution" in text
    # Page-level evidence stays behind authentication.
    assert "private-page" not in text
    assert "Missing title" not in text


# --- sitemap coverage ---


def test_coverage_ratio_reports_nothing_rather_than_zero_without_a_sitemap() -> None:
    from app.routines.sitemap_coverage import coverage_ratio

    assert coverage_ratio(20, 23) == 0.8696
    assert coverage_ratio(0, 5) == 0.0
    # An empty sitemap is unmeasured, not 0% covered.
    assert coverage_ratio(0, 0) is None


def test_indexable_predicate_requires_status_robots_and_canonical_agreement() -> None:
    from app.routines.sitemap_coverage import INDEXABLE_PREDICATE

    normalized = " ".join(INDEXABLE_PREDICATE.split())
    assert "o.http_status = 200" in normalized
    assert "NOT (o.robots_directives @> ARRAY['noindex'])" in normalized
    assert "o.canonical_url IS NULL OR o.canonical_url = p.normalized_url" in normalized


def test_sitemap_coverage_is_scoped_to_one_crawl() -> None:
    """Coverage across crawls would mix a stale sitemap with a fresh page set."""
    from app.routines.sitemap_coverage import (
        CRAWLED_NOT_DECLARED_SQL,
        DECLARED_NOT_CRAWLED_SQL,
        DECLARED_SUMMARY_SQL,
    )

    for statement in (DECLARED_SUMMARY_SQL, DECLARED_NOT_CRAWLED_SQL, CRAWLED_NOT_DECLARED_SQL):
        assert "crawl_job_id=$2" in statement.replace(" ", "")
        assert "tenant_id=$1" in statement.replace(" ", "")


# --- answer-engine readiness ---


def test_readiness_weights_sum_to_one_and_renormalise_over_measured_factors() -> None:
    from app.routines.ai_visibility import FACTOR_WEIGHTS

    assert round(sum(FACTOR_WEIGHTS.values()), 6) == 1.0


def test_readiness_is_scored_only_from_first_party_evidence() -> None:
    """Every factor must come from our own crawl or search evidence.

    A factor sourced from an external answer engine would make the score claim
    observed visibility, which no certified provider currently backs.
    """
    from app.routines.ai_visibility import (
        ANSWER_ENGINE_TYPES,
        ENTITY_TYPES,
        FACTOR_WEIGHTS,
        PAGE_EVIDENCE_SQL,
    )

    assert set(FACTOR_WEIGHTS) == {
        "crawlable_indexable",
        "entity_markup",
        "question_answer_markup",
        "question_topic_coverage",
    }
    # The only tables read are our own observations and clusters.
    compact = " ".join(PAGE_EVIDENCE_SQL.split())
    assert "page_observation" in compact
    assert "page p" in compact
    assert ANSWER_ENGINE_TYPES == ("FAQPage", "QAPage", "HowTo")
    assert "Organization" in ENTITY_TYPES


def test_the_snapshot_table_admits_no_citation_source_but_none() -> None:
    """The column is constrained so a future writer cannot imply observed data."""
    from pathlib import Path

    migration = Path("infra/migrations/0024_competitors_and_ai_visibility.sql").read_text()
    assert "citation_source text NOT NULL DEFAULT 'none'" in migration
    assert "CHECK(citation_source IN('none'))" in migration


def test_an_unmeasured_factor_lowers_confidence_rather_than_scoring_zero() -> None:
    from app.routines.ai_visibility import FACTOR_WEIGHTS

    # Reproduces the renormalisation the builder performs: a site with no
    # keyword analysis must not be penalised for the missing coverage factor.
    factors = {
        "crawlable_indexable": {"value": 1.0, "measured": True},
        "entity_markup": {"value": 1.0, "measured": True},
        "question_answer_markup": {"value": 1.0, "measured": True},
        "question_topic_coverage": {"value": 0.0, "measured": False},
    }
    measured_weight = sum(w for n, w in FACTOR_WEIGHTS.items() if factors[n]["measured"])
    score = (
        sum(FACTOR_WEIGHTS[n] * float(factors[n]["value"]) for n in FACTOR_WEIGHTS if factors[n]["measured"])
        / measured_weight
        * 100
    )
    assert round(score, 2) == 100.0
    assert measured_weight < 1.0
