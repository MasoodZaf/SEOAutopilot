"""Connecting a repository the way Claude or ChatGPT does, against PostgreSQL.

The person signs in with GitHub, installs the app if they have not, and picks a
repository from a list. What is pinned here is what makes that list
trustworthy now that the app is public and anybody can install it:

- the repositories offered are the ones GitHub says *the signed-in person* can
  push to -- read access and archived repositories are not offered;
- a pick is looked up in that list by GitHub's numeric id, and nothing else can
  be connected: not a repository from another installation, not one offered to
  a different admin of the same workspace, not one in another workspace;
- the installation is asked again at the moment of binding, so a grant narrowed
  in between is honoured;
- a person with no installation is sent to install the app and back through
  sign-in on the same state, and the loop is bounded.
"""

from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.context import Role, TenantContext
from app.services.github_app import GitHubUserClient
from app.services.github_connector import GitHubConnectorService, GitHubSignInCallbackService
from app.services.tenant_credentials import GitHubAppCredential
from tests.conftest import requires_database
from tests.integration import test_github_connector as github
from tests.integration.test_github_connector import APP_PEM, app_settings, writable

pytestmark = [pytest.mark.asyncio, requires_database]

acme = github.acme
other = github.other

INSTALLATION = 4242
ELSEWHERE = 9191

WRITABLE = {
    "id": 101,
    "full_name": "MasoodZaf/mindTools",
    "default_branch": "trunk",
    "private": True,
    "archived": False,
    "permissions": {"admin": False, "push": True, "pull": True},
}
READ_ONLY = {
    "id": 202,
    "full_name": "SomeOrg/website",
    "default_branch": "main",
    "private": True,
    "archived": False,
    "permissions": {"admin": False, "push": False, "pull": True},
}
ARCHIVED = {**WRITABLE, "id": 303, "full_name": "MasoodZaf/old", "archived": True}


def fake_github(
    *,
    installations: list[int] | None = None,
    user_repositories: list[dict] | None = None,
    granted: list[str] | None = None,
    permissions: dict[str, str] | None = None,
    code_ok: bool = True,
) -> httpx.AsyncClient:
    """GitHub as the person and as the app, with no network."""
    installed = [INSTALLATION] if installations is None else installations
    listed = [WRITABLE, READ_ONLY, ARCHIVED] if user_repositories is None else user_repositories
    reachable = ["MasoodZaf/mindTools"] if granted is None else granted
    allowed = permissions or {"contents": "write", "pull_requests": "write", "metadata": "read"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "github.com" and path == "/login/oauth/access_token":
            if not code_ok:
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": "ghu_person", "token_type": "bearer"})
        auth = request.headers.get("Authorization")
        if path == "/user/installations":
            assert auth == "Bearer ghu_person"
            return httpx.Response(
                200,
                json={
                    "total_count": len(installed),
                    "installations": [
                        {"id": item, "account": {"login": "MasoodZaf"}} for item in installed
                    ],
                },
            )
        if path.startswith("/user/installations/") and path.endswith("/repositories"):
            assert auth == "Bearer ghu_person"
            return httpx.Response(200, json={"repositories": listed})
        if path == f"/app/installations/{INSTALLATION}":
            return httpx.Response(
                200,
                json={"id": INSTALLATION, "permissions": allowed, "account": {"login": "MasoodZaf"}},
            )
        if path == f"/app/installations/{INSTALLATION}/access_tokens":
            return httpx.Response(
                201,
                json={
                    "token": "ghs_minted",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "repositories": [{"full_name": name} for name in reachable],
                },
            )
        raise AssertionError(f"unexpected GitHub call: {request.method} {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


APP = GitHubAppCredential(
    "123456", "seo-autopilot", APP_PEM, "platform", "Iv1.testclient", "s" * 40
)


def service(session, ids: dict[str, UUID], actor: UUID | None = None) -> GitHubConnectorService:
    return GitHubConnectorService(
        session,
        TenantContext(
            tenant_id=ids["tenant_id"],
            actor_id=actor or ids["actor_id"],
            role=Role.OWNER,
            trace_id="integration",
        ),
    )


async def committed(app_engine, tenant_id: UUID, work):
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
        )
        return await work(session)


async def start(app_engine, ids) -> str:
    async def work(session):
        url, _ = await service(session, ids).begin_sign_in(ids["site_id"], app_settings())
        return url

    url = await committed(app_engine, ids["tenant_id"], work)
    return parse_qs(urlsplit(url).query)["state"][0]


async def callback(app_engine, state: str, code: str | None, client: httpx.AsyncClient):
    """GitHub's redirect: no session, so the state row names the tenant."""
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(text("SELECT set_config('app.oauth_callback', 'on', true)"))

        async def app_for(_tenant_id: UUID) -> GitHubAppCredential:
            return APP

        return await GitHubSignInCallbackService(
            session,
            app_factory=app_for,
            user_client_factory=lambda app: GitHubUserClient(
                client, app.client_id, app.client_secret, "http://cb"
            ),
            settings=app_settings(),
        ).complete(state, code, "trace")


async def choices(app_engine, ids, actor: UUID | None = None):
    async def work(session):
        return await service(session, ids, actor).repository_choices(ids["site_id"])

    return await committed(app_engine, ids["tenant_id"], work)


async def choose(app_engine, ids, repository_id: int, client, actor: UUID | None = None, **kw):
    async def work(session):
        return await service(session, ids, actor).choose_repository(
            ids["site_id"],
            repository_id,
            kw.get("base_branch", ""),
            kw.get("path_template", "CalcHive/{path}.html"),
            app_settings(),
            client,
        )

    return await committed(app_engine, ids["tenant_id"], work)


async def connector_row(engine, ids):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        return (
            await session.execute(
                text(
                    "SELECT status, provider_key, external_account_ref, secret_ref, config_json,"
                    " (SELECT count(*) FROM connector_secret s"
                    "   WHERE s.connector_id=c.id AND s.revoked_at IS NULL) AS live_secrets"
                    " FROM connector c WHERE tenant_id=:t AND type='github_repository'"
                ),
                {"t": ids["tenant_id"]},
            )
        ).one()


async def signed_in(app_engine, ids, client=None) -> None:
    state = await start(app_engine, ids)
    outcome = await callback(app_engine, state, "code-1", client or fake_github())
    assert outcome.site_id == ids["site_id"]


async def test_only_repositories_the_person_can_push_to_are_offered(app_engine, acme) -> None:
    await signed_in(app_engine, acme)
    listed, expires_at = await choices(app_engine, acme)

    assert [item["full_name"] for item in listed] == ["MasoodZaf/mindTools"]
    assert listed[0]["installation_id"] == INSTALLATION
    assert expires_at is not None


async def test_picking_a_repository_connects_it_with_no_token_stored(
    app_engine, engine, acme
) -> None:
    await signed_in(app_engine, acme)
    await choose(app_engine, acme, WRITABLE["id"], fake_github())

    row = await connector_row(engine, acme)
    assert (row.status, row.provider_key) == ("active", "github_app")
    assert row.external_account_ref == "MasoodZaf/mindTools"
    assert row.secret_ref is None
    assert row.config_json["installation_id"] == INSTALLATION
    assert row.config_json["repository_id"] == WRITABLE["id"]
    # Left blank, the branch is the repository's own default, as GitHub said.
    assert row.config_json["base_branch"] == "trunk"
    assert row.config_json["path_template"] == "CalcHive/{path}.html"


async def test_picking_replaces_and_destroys_a_stored_token(app_engine, engine, acme) -> None:
    async def with_token(session):
        await github.connect(session, acme, writable(), path_template="Old/{path}.html")

    await committed(app_engine, acme["tenant_id"], with_token)
    await signed_in(app_engine, acme)
    # Signing in alone changes nothing about a site that deploys today.
    assert (await connector_row(engine, acme)).provider_key == "github_pat"

    await choose(app_engine, acme, WRITABLE["id"], fake_github())
    row = await connector_row(engine, acme)
    assert row.provider_key == "github_app"
    assert row.live_secrets == 0


async def test_a_repository_the_person_can_only_read_cannot_be_connected(
    app_engine, engine, acme
) -> None:
    """The escalation this closes: read access plus a public app is not write access."""
    await signed_in(app_engine, acme)
    with pytest.raises(HTTPException) as refused:
        await choose(
            app_engine, acme, READ_ONLY["id"], fake_github(granted=["SomeOrg/website"])
        )
    assert refused.value.detail == "github_repository_not_offered"
    assert (await connector_row(engine, acme)).status == "pending_authorization"


async def test_another_admin_cannot_pick_from_my_list(app_engine, acme) -> None:
    await signed_in(app_engine, acme)
    colleague = uuid4()

    listed, _ = await choices(app_engine, acme, actor=colleague)
    assert listed == []
    with pytest.raises(HTTPException) as refused:
        await choose(app_engine, acme, WRITABLE["id"], fake_github(), actor=colleague)
    assert refused.value.detail == "github_choice_expired"


async def test_another_workspace_cannot_see_or_use_the_list(app_engine, acme, other) -> None:
    await signed_in(app_engine, acme)
    intruder = {**other, "site_id": acme["site_id"], "actor_id": acme["actor_id"]}

    with pytest.raises(HTTPException) as refused:
        await choices(app_engine, intruder)
    assert refused.value.detail == "site_not_found"
    with pytest.raises(HTTPException) as refused:
        await choose(app_engine, intruder, WRITABLE["id"], fake_github())
    assert refused.value.detail == "site_not_found"


async def test_a_grant_narrowed_after_sign_in_is_honoured(app_engine, engine, acme) -> None:
    await signed_in(app_engine, acme)
    with pytest.raises(HTTPException) as refused:
        await choose(app_engine, acme, WRITABLE["id"], fake_github(granted=["MasoodZaf/other"]))
    assert refused.value.detail == "github_repository_not_installed"
    assert (await connector_row(engine, acme)).status == "pending_authorization"


async def test_an_installation_that_cannot_open_pull_requests_is_refused(app_engine, acme) -> None:
    await signed_in(app_engine, acme)
    client = fake_github(permissions={"contents": "write", "pull_requests": "read"})
    with pytest.raises(HTTPException) as refused:
        await choose(app_engine, acme, WRITABLE["id"], client)
    assert refused.value.detail == "github_installation_permissions_insufficient"


async def test_a_pick_is_spent_once(app_engine, acme) -> None:
    await signed_in(app_engine, acme)
    await choose(app_engine, acme, WRITABLE["id"], fake_github())
    with pytest.raises(HTTPException) as refused:
        await choose(app_engine, acme, WRITABLE["id"], fake_github())
    assert refused.value.detail == "github_choice_expired"


async def test_no_installation_sends_the_person_to_install_and_back(app_engine, acme) -> None:
    state = await start(app_engine, acme)

    first = await callback(app_engine, state, "code-1", fake_github(installations=[]))
    assert first.redirect_url is not None
    assert first.redirect_url.startswith(
        "https://github.com/apps/seo-autopilot/installations/new?"
    )
    assert parse_qs(urlsplit(first.redirect_url).query)["state"] == [state]

    # Back from the install page: no code, so sign in again on the same state.
    second = await callback(app_engine, state, None, fake_github())
    assert second.redirect_url is not None
    assert second.redirect_url.startswith("https://github.com/login/oauth/authorize?")

    third = await callback(app_engine, state, "code-2", fake_github())
    assert third.site_id == acme["site_id"]


async def test_the_install_round_trip_is_bounded(app_engine, acme) -> None:
    state = await start(app_engine, acme)
    for _ in range(3):
        await callback(app_engine, state, None, fake_github())
    with pytest.raises(HTTPException) as refused:
        await callback(app_engine, state, None, fake_github())
    assert refused.value.detail == "github_installation_not_found"


async def test_a_sign_in_state_is_spent_once(app_engine, acme) -> None:
    state = await start(app_engine, acme)
    await callback(app_engine, state, "code-1", fake_github())
    with pytest.raises(HTTPException) as refused:
        await callback(app_engine, state, "code-1", fake_github())
    assert refused.value.detail == "installation_state_invalid"


async def test_a_code_github_rejects_offers_nothing(app_engine, acme) -> None:
    state = await start(app_engine, acme)
    with pytest.raises(HTTPException) as refused:
        await callback(app_engine, state, "stale", fake_github(code_ok=False))
    assert refused.value.detail == "github_authorization_failed"
    listed, _ = await choices(app_engine, acme)
    assert listed == []
