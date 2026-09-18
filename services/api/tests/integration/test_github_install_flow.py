"""The GitHub App install, start to finish, against PostgreSQL with RLS on.

`GitHubInstallationCallbackService.complete` had no test at all until this
file, and has never run against real GitHub. It is the "connect a repository in
one step" path a working SEO tester asked for, so its promises are pinned here
with the real `GitHubAppClient` talking to a mocked GitHub:

- an install that is started and abandoned changes nothing about a site that is
  already deploying with a stored token;
- a completed install applies the branch and path the tenant asked for, stops
  referencing the stored token, and destroys it;
- an install that did not grant the named repository, or the permissions a
  deployment needs, is refused rather than recorded as connected.
"""

from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.github_app import GitHubAppClient
from app.services.github_connector import GitHubInstallationCallbackService
from tests.conftest import requires_database
from tests.integration import test_github_connector as github
from tests.integration.test_github_connector import (
    APP_PEM,
    app_settings,
    service,
    writable,
)

pytestmark = [pytest.mark.asyncio, requires_database]

acme = github.acme

INSTALLATION = 4242


def fake_github(
    *,
    repositories: list[str],
    permissions: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    granted = permissions or {"contents": "write", "pull_requests": "write", "metadata": "read"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/app/installations/{INSTALLATION}":
            return httpx.Response(
                200, json={"id": INSTALLATION, "permissions": granted, "account": {"login": "MasoodZaf"}}
            )
        if path == f"/app/installations/{INSTALLATION}/access_tokens":
            return httpx.Response(
                201,
                json={
                    "token": "ghs_minted",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "repositories": [{"full_name": name} for name in repositories],
                },
            )
        raise AssertionError(f"unexpected GitHub call: {request.method} {path}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def committed(app_engine, tenant_id: UUID, work):
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
        )
        return await work(session)


async def start(app_engine, ids, path_template: str = "CalcHive/{path}.html") -> str:
    async def work(session):
        _, url, _ = await service(session, ids).begin_app_installation(
            ids["site_id"], "MasoodZaf/mindTools", "main", path_template, app_settings()
        )
        return url

    url = await committed(app_engine, ids["tenant_id"], work)
    return parse_qs(urlsplit(url).query)["state"][0]


async def finish(app_engine, state: str, client: httpx.AsyncClient):
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(text("SELECT set_config('app.oauth_callback', 'on', true)"))
        app = GitHubAppClient(client, "123456", APP_PEM)
        return await GitHubInstallationCallbackService(session, app).complete(
            state, INSTALLATION, "trace"
        )


async def connector_row(engine, ids):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        return (
            await session.execute(
                text(
                    "SELECT status, provider_key, secret_ref, config_json,"
                    " (SELECT count(*) FROM connector_secret s"
                    "   WHERE s.connector_id=c.id AND s.revoked_at IS NULL) AS live_secrets"
                    " FROM connector c WHERE tenant_id=:t AND type='github_repository'"
                ),
                {"t": ids["tenant_id"]},
            )
        ).one()


async def connect_token(app_engine, ids) -> None:
    async def work(session):
        await github.connect(session, ids, writable(), path_template="Old/{path}.html")

    await committed(app_engine, ids["tenant_id"], work)


async def test_an_abandoned_install_leaves_a_deploying_site_alone(
    app_engine, engine, acme
) -> None:
    await connect_token(app_engine, acme)
    await start(app_engine, acme, path_template="New/{path}.html")
    # ...and the person closes GitHub's tab.

    row = await connector_row(engine, acme)
    assert row.status == "active"
    assert row.provider_key == "github_pat"
    assert row.config_json["path_template"] == "Old/{path}.html"
    assert row.live_secrets == 1


async def test_a_completed_install_replaces_the_stored_token(app_engine, engine, acme) -> None:
    await connect_token(app_engine, acme)
    state = await start(app_engine, acme, path_template="CalcHive/{path}.html")

    await finish(app_engine, state, fake_github(repositories=["MasoodZaf/mindTools"]))

    row = await connector_row(engine, acme)
    assert row.status == "active"
    assert row.provider_key == "github_app"
    assert row.secret_ref is None
    assert row.live_secrets == 0
    assert row.config_json["path_template"] == "CalcHive/{path}.html"
    assert row.config_json["installation_id"] == INSTALLATION


async def test_a_first_connection_needs_nothing_but_the_install(app_engine, engine, acme) -> None:
    state = await start(app_engine, acme)
    await finish(app_engine, state, fake_github(repositories=["MasoodZaf/mindTools"]))

    row = await connector_row(engine, acme)
    assert (row.status, row.provider_key) == ("active", "github_app")


async def test_an_install_without_the_named_repository_is_refused(
    app_engine, engine, acme
) -> None:
    state = await start(app_engine, acme)
    with pytest.raises(HTTPException) as refused:
        await finish(app_engine, state, fake_github(repositories=["MasoodZaf/elsewhere"]))
    assert refused.value.detail == "github_repository_not_installed"
    assert (await connector_row(engine, acme)).status == "pending_authorization"


async def test_an_install_that_cannot_open_pull_requests_is_refused(
    app_engine, engine, acme
) -> None:
    state = await start(app_engine, acme)
    client = fake_github(
        repositories=["MasoodZaf/mindTools"],
        permissions={"contents": "write", "pull_requests": "read"},
    )
    with pytest.raises(HTTPException) as refused:
        await finish(app_engine, state, client)
    assert refused.value.detail == "github_installation_permissions_insufficient"


async def test_a_state_is_spent_once(app_engine, engine, acme) -> None:
    state = await start(app_engine, acme)
    await finish(app_engine, state, fake_github(repositories=["MasoodZaf/mindTools"]))
    with pytest.raises(HTTPException) as refused:
        await finish(app_engine, state, fake_github(repositories=["MasoodZaf/mindTools"]))
    assert refused.value.detail == "installation_state_invalid"


