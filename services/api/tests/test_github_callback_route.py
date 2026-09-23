"""What GitHub's redirect does before any state is read.

The callback is reached from GitHub's pages with no session, and a person is
always at the end of it. Every one of these returns them to the connections
page with something the page can explain -- and none of them reads the
`installation_id` GitHub appends, which once the app is public anybody can
forge.
"""

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes import connectors
from tests.integration.test_github_connector import app_settings


async def call(**query):
    params = {"state": None, "code": None, "setup_action": None, "error": None, **query}
    with patch.object(connectors, "get_settings", return_value=app_settings()):
        return await connectors.github_callback(AsyncMock(), **params)


def target(response) -> str:
    return response.headers["location"]


@pytest.mark.asyncio
async def test_cancelling_on_github_returns_to_the_page() -> None:
    response = await call(state="s" * 43, error="access_denied")
    assert response.status_code == 303
    assert target(response).endswith("/settings/connectors?error=github_sign_in_cancelled")


@pytest.mark.asyncio
async def test_an_install_awaiting_an_org_owner_says_so() -> None:
    response = await call(setup_action="request")
    assert target(response).endswith("?error=github_installation_requested")


@pytest.mark.asyncio
async def test_a_return_without_our_state_binds_nothing() -> None:
    response = await call(setup_action="update")
    assert target(response).endswith("/settings/connectors?github=updated")


def test_the_installation_id_github_appends_is_never_read() -> None:
    assert "installation_id" not in inspect.signature(connectors.github_callback).parameters
