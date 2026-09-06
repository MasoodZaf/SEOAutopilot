"""Deterministic routine scheduling.

Pure functions: the same schedule and the same reference instant always yield
the same next occurrence. The scheduler advances `next_run_at` from the slot it
just claimed, never from wall-clock time, so a worker outage produces a
predictable catch-up sequence rather than a drifting one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

# Schedules are anchored to whole minutes in UTC.
MAX_CATCH_UP_SLOTS = 8


class RoutineKind(StrEnum):
    SITE_AUDIT = "site_audit"
    KEYWORD_REFRESH = "keyword_refresh"
    SITEMAP_COVERAGE = "sitemap_coverage"
    CONTENT_BRIEFS = "content_briefs"
    COMPETITOR_SCAN = "competitor_scan"
    AI_VISIBILITY_SCAN = "ai_visibility_scan"
    WEEKLY_REPORT = "weekly_report"
    SEARCH_CONSOLE_SYNC = "search_console_sync"


class Cadence(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True, slots=True)
class RoutineSchedule:
    cadence: Cadence
    hour_utc: int = 6
    minute_utc: int = 0
    # ISO weekday, 1=Monday..7=Sunday. Required for weekly.
    isodow: int | None = None
    # Capped at 28 so the slot exists in every month.
    dom: int | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.hour_utc <= 23:
            raise ValueError("schedule_hour_out_of_range")
        if not 0 <= self.minute_utc <= 59:
            raise ValueError("schedule_minute_out_of_range")
        if self.cadence is Cadence.WEEKLY:
            if self.isodow is None:
                raise ValueError("schedule_isodow_required")
            if not 1 <= self.isodow <= 7:
                raise ValueError("schedule_isodow_out_of_range")
        if self.cadence is Cadence.MONTHLY:
            if self.dom is None:
                raise ValueError("schedule_dom_required")
            if not 1 <= self.dom <= 28:
                raise ValueError("schedule_dom_out_of_range")


def _anchor(moment: datetime, schedule: RoutineSchedule) -> datetime:
    return moment.astimezone(UTC).replace(
        hour=schedule.hour_utc,
        minute=schedule.minute_utc,
        second=0,
        microsecond=0,
    )


def next_occurrence(schedule: RoutineSchedule, after: datetime) -> datetime:
    """Return the first scheduled instant strictly after `after`."""
    reference = after.astimezone(UTC)
    candidate = _anchor(reference, schedule)

    if schedule.cadence is Cadence.DAILY:
        if candidate <= reference:
            candidate += timedelta(days=1)
        return candidate

    if schedule.cadence is Cadence.WEEKLY:
        assert schedule.isodow is not None
        delta = (schedule.isodow - candidate.isoweekday()) % 7
        candidate += timedelta(days=delta)
        if candidate <= reference:
            candidate += timedelta(days=7)
        return candidate

    assert schedule.dom is not None
    candidate = candidate.replace(day=schedule.dom)
    if candidate <= reference:
        year, month = candidate.year, candidate.month + 1
        if month > 12:
            year, month = year + 1, 1
        candidate = candidate.replace(year=year, month=month)
    return candidate


def advance_from_slot(
    schedule: RoutineSchedule, claimed_slot: datetime, now: datetime
) -> datetime:
    """Advance past a claimed slot, collapsing a long backlog.

    After downtime a routine could have many missed slots. Replaying all of them
    would burst crawl and provider budget, so at most MAX_CATCH_UP_SLOTS are
    walked before the schedule realigns to the present.
    """
    candidate = next_occurrence(schedule, claimed_slot)
    for _ in range(MAX_CATCH_UP_SLOTS):
        if candidate > now:
            return candidate
        candidate = next_occurrence(schedule, candidate)
    return next_occurrence(schedule, now)


def initial_run_at(schedule: RoutineSchedule, now: datetime | None = None) -> datetime:
    return next_occurrence(schedule, now or datetime.now(UTC))
