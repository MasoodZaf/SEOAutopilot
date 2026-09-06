from uuid import uuid4

import pytest
from app.tenancy import CLEAR_SCOPE, SET_SCOPE, tenant_scope


class RecordingConnection:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fail_on = fail_on

    async def execute(self, sql: str, *args: object) -> None:
        self.calls.append((sql, args))


@pytest.mark.asyncio
async def test_scope_is_set_then_cleared() -> None:
    connection = RecordingConnection()
    tenant_id = uuid4()
    async with tenant_scope(connection, tenant_id):
        assert connection.calls == [(SET_SCOPE, (str(tenant_id),))]
    assert connection.calls[-1] == (CLEAR_SCOPE, ())


@pytest.mark.asyncio
async def test_scope_is_cleared_even_when_the_unit_of_work_raises() -> None:
    """A failed job must not leave its tenant's scope on a pooled connection."""
    connection = RecordingConnection()
    with pytest.raises(RuntimeError):
        async with tenant_scope(connection, uuid4()):
            raise RuntimeError("job failed")
    assert connection.calls[-1] == (CLEAR_SCOPE, ())
