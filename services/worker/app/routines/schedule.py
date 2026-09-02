"""Deterministic routine scheduling.

Mirror of `services/api/app/domain/routines.py`. The two services do not share a
Python package, so the arithmetic is duplicated and covered by an identical test
vector on both sides; change them together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MAX_CATCH_UP_SLOTS = 8


@dataclass(frozen=True, slots=True)
class RoutineSchedule:
    cadence: str
    hour_utc: int = 6
    minute_utc: int = 0
    isodow: int | None = None
    dom: int | None = None


def _anchor(moment: datetime, schedule: RoutineSchedule) -> datetime:
    return moment.astimezone(UTC).replace(
        hour=schedule.hour_utc, minute=schedule.minute_utc, second=0, microsecond=0
    )


def next_occurrence(schedule: RoutineSchedule, after: datetime) -> datetime:
    reference = after.astimezone(UTC)
    candidate = _anchor(reference, schedule)

    if schedule.cadence == "daily":
        if candidate <= reference:
            candidate += timedelta(days=1)
        return candidate

    if schedule.cadence == "weekly":
        if schedule.isodow is None:
            raise ValueError("schedule_isodow_required")
        candidate += timedelta(days=(schedule.isodow - candidate.isoweekday()) % 7)
        if candidate <= reference:
            candidate += timedelta(days=7)
        return candidate

    if schedule.cadence != "monthly":
        raise ValueError("unsupported_cadence")
    if schedule.dom is None:
        raise ValueError("schedule_dom_required")
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

    Replaying every missed slot after an outage would burst crawl and provider
    budget, so at most MAX_CATCH_UP_SLOTS are walked before realigning to now.
    """
    candidate = next_occurrence(schedule, claimed_slot)
    for _ in range(MAX_CATCH_UP_SLOTS):
        if candidate > now:
            return candidate
        candidate = next_occurrence(schedule, candidate)
    return next_occurrence(schedule, now)


def schedule_from_row(row: object) -> RoutineSchedule:
    get = row.__getitem__  # type: ignore[attr-defined]
    return RoutineSchedule(
        cadence=str(get("cadence")),
        hour_utc=int(get("schedule_hour_utc")),
        minute_utc=int(get("schedule_minute_utc")),
        isodow=get("schedule_isodow"),
        dom=get("schedule_dom"),
    )
