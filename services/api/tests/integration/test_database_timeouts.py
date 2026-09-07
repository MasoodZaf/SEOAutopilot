"""Bounds on how long a statement, a lock wait, or a dead transaction can last.

Every timeout on this database was 0. That was survivable while nothing took a
row lock. Membership changes now read the row they modify `FOR UPDATE` and lock
the owner rows they count -- which is what makes two owners demoting each other
at the same moment safe -- and the cost of a lock is that something can wait on
it. With `lock_timeout = 0` that wait is unbounded, so one request that dies
holding its transaction open blocks every later membership change until a person
notices.

These assert the settings are actually attached to the roles the services
connect as, because a role setting that was never applied looks exactly like one
that was: both are invisible until the day something waits.
"""

import re

import pytest
from sqlalchemy import text

from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

# Generous enough not to end legitimate work, short enough to end the rest.
EXPECTED = {
    "seo_autopilot_app": {
        "statement_timeout": "60s",
        "lock_timeout": "10s",
        "idle_in_transaction_session_timeout": "120s",
    },
    "seo_autopilot_relay": {
        "statement_timeout": "120s",
        "lock_timeout": "15s",
        # Deliberately long: the reconciler holds this connection while it asks
        # GitHub about each pending revert.
        "idle_in_transaction_session_timeout": "900s",
    },
}


async def _role_settings(engine, role: str) -> dict[str, str]:
    async with engine.begin() as connection:
        raw = await connection.scalar(
            text(
                "SELECT s.setconfig FROM pg_db_role_setting s"
                " JOIN pg_roles r ON r.oid = s.setrole WHERE r.rolname = :role"
            ),
            {"role": role},
        )
    settings: dict[str, str] = {}
    for entry in raw or []:
        key, _, value = entry.partition("=")
        settings[key] = value
    return settings


async def test_both_service_roles_carry_the_timeouts(engine) -> None:
    for role, expected in EXPECTED.items():
        found = await _role_settings(engine, role)
        for key, value in expected.items():
            assert found.get(key) == value, f"{role}.{key} is {found.get(key)!r}, want {value!r}"


def _milliseconds(value: str) -> int:
    """`60s` and `1min` are the same timeout; PostgreSQL reports whichever it likes.

    A bare number is milliseconds, which is how `pg_settings.setting` reports
    these.
    """
    match = re.fullmatch(r"\s*(\d+)\s*(ms|s|min)?\s*", value)
    if match is None:
        raise ValueError(f"not a timeout: {value!r}")
    return int(match.group(1)) * {"ms": 1, "s": 1000, "min": 60_000}[match.group(2) or "ms"]


async def test_the_application_role_actually_applies_them_on_connect(app_engine) -> None:
    """A role setting takes effect at connection time, or not at all.

    Asserted through the application connection rather than from the catalogue,
    because that connection is the thing whose behaviour is in question.
    Compared in milliseconds: PostgreSQL normalises `60s` to `1min`, and a
    string comparison would fail on a setting that is perfectly correct.
    """
    async with app_engine.begin() as connection:
        for key, value in EXPECTED["seo_autopilot_app"].items():
            live = await connection.scalar(
                text("SELECT setting FROM pg_settings WHERE name = :name"), {"name": key}
            )
            assert int(live) == _milliseconds(value), (
                f"{key} is {live}ms on a live connection, want {_milliseconds(value)}ms"
            )


async def test_a_lock_wait_ends_rather_than_hanging(app_engine, engine) -> None:
    """The property the whole change exists for.

    A second transaction waiting on a row another holds must fail, not wait
    forever. `lock_timeout` is set to seconds on the role, so this lowers it for
    the test rather than making the suite wait ten of them.
    """
    async with engine.begin() as holder:
        # Lock a row nobody else in the suite touches, and keep it.
        await holder.execute(text("CREATE TEMP TABLE IF NOT EXISTS _lock_probe(id int)"))
        await holder.execute(
            text("CREATE TABLE IF NOT EXISTS lock_probe(id int PRIMARY KEY)")
        )
        await holder.execute(
            text("INSERT INTO lock_probe(id) VALUES (1) ON CONFLICT DO NOTHING")
        )

    async with engine.connect() as holder:
        await holder.execute(text("BEGIN"))
        await holder.execute(text("SELECT id FROM lock_probe WHERE id = 1 FOR UPDATE"))
        try:
            async with app_engine.connect() as waiter:
                await waiter.execute(text("SET lock_timeout = '250ms'"))
                await waiter.execute(text("BEGIN"))
                with pytest.raises(Exception) as raised:
                    await waiter.execute(
                        text("SELECT id FROM lock_probe WHERE id = 1 FOR UPDATE")
                    )
                assert "lock timeout" in str(raised.value).lower()
                await waiter.execute(text("ROLLBACK"))
        finally:
            await holder.execute(text("ROLLBACK"))

    async with engine.begin() as cleanup:
        await cleanup.execute(text("DROP TABLE IF EXISTS lock_probe"))
