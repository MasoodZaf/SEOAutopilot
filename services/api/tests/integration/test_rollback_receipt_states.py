"""A rollback that was only requested must be storable as such.

The GitHub adapter has two rollback outcomes and reported `applied` for both.
Closing a pull request that never merged really does undo the change; opening a
revert pull request does not -- the deployed content stays live until a person
merges. The service wrote `rolled_back` either way, so on the pilot site a
deployment receipt read `rolled_back` while the page still served the deployed
content and the revert pull request sat unmerged, then closed.

The code now distinguishes them, which is only worth anything if the column can
hold the distinction. This asserts the two states against a real CHECK
constraint rather than against a mock session, where a constraint is not
something that exists.
"""

import re

import pytest
from sqlalchemy import text

from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]


async def _accepted_values(engine, table: str) -> set[str]:
    """The values the live CHECK constraint actually permits."""
    async with engine.begin() as connection:
        definition = await connection.scalar(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
            {"name": f"{table}_status_check"},
        )
    assert definition, f"{table}_status_check is missing"
    return set(re.findall(r"'([a-z_]+)'::text", definition))


async def test_a_deployment_receipt_can_say_the_rollback_is_unfinished(engine) -> None:
    allowed = await _accepted_values(engine, "deployment_receipt")

    # Both, because they mean opposite things about whether the change is live.
    assert "rollback_pending" in allowed
    assert "rolled_back" in allowed


async def test_a_rollback_receipt_can_say_it_is_waiting_on_a_person(engine) -> None:
    assert "pending" in await _accepted_values(engine, "rollback_receipt")


async def test_the_columns_are_still_closed_sets(engine) -> None:
    """A constraint widened to accept anything would prove nothing."""
    assert await _accepted_values(engine, "deployment_receipt") == {
        "pending", "applied", "failed", "rollback_pending", "rolled_back",
    }
    assert await _accepted_values(engine, "rollback_receipt") == {
        "pending", "applied", "failed",
    }
