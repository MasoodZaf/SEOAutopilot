"""Guards the one-transaction-per-request invariant that RLS depends on.

`get_tenant_session` sets `app.tenant_id` with `set_config(..., true)`, which is
transaction-local. Any service that ends the request transaction on its own --
`commit()` or `rollback()` -- drops that setting, so every later statement in
the request runs unscoped and row-level security rejects it. The failure is a
500 far from its cause, and the service tests cannot see it because they all
run against a mocked session.

A partial undo inside a request is spelled `session.begin_nested()`, which
rolls back to a savepoint and leaves the request transaction, and the tenant
setting, intact.
"""

import ast
from pathlib import Path

import pytest

SERVICES = sorted((Path(__file__).resolve().parents[1] / "app" / "services").glob("*.py"))
FORBIDDEN = {"commit", "rollback"}


def transaction_enders(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute) or function.attr not in FORBIDDEN:
            continue
        target = function.value
        # Only `self.session.commit()` / `self.session.rollback()` and friends.
        if isinstance(target, ast.Attribute) and target.attr == "session":
            found.append(f"session.{function.attr}()")
    return found


@pytest.mark.parametrize("path", SERVICES, ids=lambda item: item.name)
def test_no_service_ends_the_request_transaction(path: Path) -> None:
    offenders = transaction_enders(path.read_text())
    assert offenders == [], (
        f"{path.name} calls {offenders}, which ends the request transaction and "
        "drops the tenant setting RLS reads. Stage writes with flush(), and use "
        "session.begin_nested() when a write may need to be undone on its own."
    )
