from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.api.schemas import CadenceName, NotificationChannelCreate, RoutineKindName, RoutineUpsert
from app.domain.routines import (
    Cadence,
    RoutineSchedule,
    advance_from_slot,
    initial_run_at,
    next_occurrence,
)
from app.services.notifications import destination_hint


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_daily_schedule_advances_to_the_next_day_only_after_the_slot_passes() -> None:
    schedule = RoutineSchedule(cadence=Cadence.DAILY, hour_utc=6, minute_utc=30)
    assert next_occurrence(schedule, at("2026-09-02T05:00:00")) == at("2026-09-02T06:30:00")
    assert next_occurrence(schedule, at("2026-09-02T06:30:00")) == at("2026-09-03T06:30:00")
    assert next_occurrence(schedule, at("2026-09-02T23:59:00")) == at("2026-09-03T06:30:00")


def test_weekly_schedule_lands_on_the_requested_isoweekday() -> None:
    # isodow 1 is Monday; 2026-09-02 is a Wednesday.
    schedule = RoutineSchedule(cadence=Cadence.WEEKLY, hour_utc=7, isodow=1)
    occurrence = next_occurrence(schedule, at("2026-09-02T10:00:00"))
    assert occurrence == at("2026-09-07T07:00:00")
    assert occurrence.isoweekday() == 1


def test_monthly_schedule_rolls_over_the_year_boundary() -> None:
    schedule = RoutineSchedule(cadence=Cadence.MONTHLY, hour_utc=3, dom=15)
    assert next_occurrence(schedule, at("2026-12-20T00:00:00")) == at("2027-01-15T03:00:00")


def test_schedule_is_deterministic_for_the_same_reference_instant() -> None:
    schedule = RoutineSchedule(cadence=Cadence.WEEKLY, hour_utc=6, isodow=4)
    reference = at("2026-09-02T12:00:00")
    assert next_occurrence(schedule, reference) == next_occurrence(schedule, reference)


def test_backlog_is_collapsed_rather_than_replayed() -> None:
    """A long outage must not queue one run per missed day."""
    schedule = RoutineSchedule(cadence=Cadence.DAILY, hour_utc=6)
    missed_slot = at("2026-01-01T06:00:00")
    now = at("2026-09-02T09:00:00")
    following = advance_from_slot(schedule, missed_slot, now)
    assert following > now
    assert following == at("2026-09-03T06:00:00")


def test_schedule_validation_fails_closed() -> None:
    with pytest.raises(ValueError, match="schedule_isodow_required"):
        RoutineSchedule(cadence=Cadence.WEEKLY, hour_utc=6)
    with pytest.raises(ValueError, match="schedule_dom_required"):
        RoutineSchedule(cadence=Cadence.MONTHLY, hour_utc=6)
    with pytest.raises(ValueError, match="schedule_hour_out_of_range"):
        RoutineSchedule(cadence=Cadence.DAILY, hour_utc=24)
    # Day 29-31 is rejected so every month has the slot.
    with pytest.raises(ValueError, match="schedule_dom_out_of_range"):
        RoutineSchedule(cadence=Cadence.MONTHLY, dom=31)
    with pytest.raises(ValueError, match="schedule_isodow_out_of_range"):
        RoutineSchedule(cadence=Cadence.WEEKLY, isodow=8)


def test_initial_run_at_is_always_in_the_future() -> None:
    now = at("2026-09-02T06:00:00")
    schedule = RoutineSchedule(cadence=Cadence.DAILY, hour_utc=6)
    assert initial_run_at(schedule, now) > now


def test_routine_command_requires_cadence_specific_fields() -> None:
    with pytest.raises(ValidationError):
        RoutineUpsert(kind=RoutineKindName.WEEKLY_REPORT, cadence=CadenceName.WEEKLY)
    with pytest.raises(ValidationError):
        RoutineUpsert(kind=RoutineKindName.SITE_AUDIT, cadence=CadenceName.MONTHLY)
    accepted = RoutineUpsert(
        kind=RoutineKindName.WEEKLY_REPORT, cadence=CadenceName.WEEKLY, schedule_isodow=1
    )
    assert accepted.schedule_isodow == 1


def test_webhook_url_must_be_public_https_without_credentials() -> None:
    for rejected in (
        "http://hooks.slack.com/services/AAA/BBB",
        "https://user:pass@hooks.slack.com/services/AAA/BBB",
        "https://localhost/services/AAA",
        "https://intranet.local/hook",
    ):
        with pytest.raises(ValidationError):
            NotificationChannelCreate(kind="slack_webhook", name="ops", webhook_url=rejected)

    accepted = NotificationChannelCreate(
        kind="slack_webhook", name="ops", webhook_url="https://hooks.slack.com/services/AAA/BBBCCC"
    )
    assert accepted.webhook_url.get_secret_value().startswith("https://")


def test_destination_hint_never_leaks_a_replayable_url() -> None:
    url = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXsecret"
    hint = destination_hint(url)
    assert hint.startswith("hooks.slack.com")
    assert "secret" not in hint
    assert "T00000000" not in hint


def test_notifications_require_a_secret_backend_and_a_managed_one_in_production() -> None:
    from pydantic import ValidationError as SettingsValidationError

    from app.core.config import Settings

    with pytest.raises(SettingsValidationError, match="connector secret backend"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="test",
            cursor_signing_key="c" * 32,
            notifications_enabled=True,
        )
    with pytest.raises(SettingsValidationError, match="managed connector secret backend"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="production",
            cursor_signing_key="c" * 32,
            notifications_enabled=True,
            connector_secret_backend="database_envelope",
        )
