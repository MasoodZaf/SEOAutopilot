"""Whose credential a deployment uses, against PostgreSQL.

The defect this replaces is not subtle and not hypothetical. `GITHUB_TOKEN` was
one process-wide setting read by four call sites, so every tenant's approved
change would have been pushed with the same credential, to the same repository,
whoever owned the site. The tests that mattered all ran against an `AsyncMock`
session, where a query that ignores `tenant_id` looks exactly like one that
does not.

These run against a real database with row-level security on, and the first
thing they assert is the thing the old design could not do at all: two tenants,
two connectors, two different tokens, and neither one reachable from the other.
"""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.services.connector_secrets import DatabaseEnvelopeSecretStore
from app.services.github_connector import (
    GITHUB_CONNECTOR,
    PROVIDER_APP,
    GitHubConnectorService,
    RepositoryFacts,
)
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

ENCRYPTION_KEY = b"k" * 32
APP_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
APP_PEM = APP_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "development",
        "cursor_signing_key": "x" * 32,
        "deployments_enabled": True,
        "connector_secret_backend": "database_envelope",
        "connector_secret_encryption_key": "a" * 43 + "=",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def app_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "github_connectors_enabled": True,
        "github_app_id": "123456",
        "github_app_slug": "seo-autopilot",
        "github_app_private_key": APP_PEM,
        "github_app_client_id": "Iv1.testclient",
        "github_app_client_secret": "s" * 40,
    }
    values.update(overrides)
    return settings(**values)


class FakeProbe:
    """GitHub's answer about a repository, without the network."""

    def __init__(self, facts: RepositoryFacts) -> None:
        self.facts = facts
        self.seen: list[tuple[str, str]] = []

    async def inspect(self, slug: str, token: str) -> RepositoryFacts:
        self.seen.append((slug, token))
        return self.facts


def writable(full_name: str = "MasoodZaf/mindTools") -> FakeProbe:
    return FakeProbe(
        RepositoryFacts(
            full_name=full_name, default_branch="main", can_push=True, archived=False
        )
    )


def minting(token: str, repositories: list[str] | None) -> httpx.AsyncClient:
    """A transport that mints one installation token and nothing else."""
    body: dict[str, object] = {"token": token, "expires_at": "2099-01-01T00:00:00Z"}
    if repositories is not None:
        body["repositories"] = [{"full_name": name} for name in repositories]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/access_tokens")
        return httpx.Response(201, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def unused() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError(f"no HTTP call expected, got {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _seed(session, ids: dict[str, UUID], host: str) -> None:
    await session.execute(
        text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'gh','active')"),
        {"id": ids["tenant_id"], "slug": f"gh-{ids['tenant_id'].hex[:8]}"},
    )
    await session.execute(
        text(
            "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,"
            "verified_at) VALUES(:id,:tenant_id,'s',:origin,:host,'recommend','active',:verified)"
        ),
        {
            "id": ids["site_id"],
            "tenant_id": ids["tenant_id"],
            "origin": f"https://{host}",
            "host": host,
            "verified": datetime(2026, 1, 1, tzinfo=UTC),
        },
    )


async def _teardown(session, tenant_id: UUID) -> None:
    for table in (
        "connector_secret",
        "connector_oauth_state",
        "connector",
        "outbox_event",
        "audit_event",
        "site",
    ):
        await session.execute(
            text(f"DELETE FROM {table} WHERE tenant_id=:tenant_id"), {"tenant_id": tenant_id}
        )
    await session.execute(text("DELETE FROM tenant WHERE id=:id"), {"id": tenant_id})


def _fixture(host: str):
    @pytest_asyncio.fixture
    async def _make(engine):
        ids = {"tenant_id": uuid4(), "site_id": uuid4(), "actor_id": uuid4()}
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session, session.begin():
            await _seed(session, ids, host)
        yield ids
        async with factory() as session, session.begin():
            await _teardown(session, ids["tenant_id"])

    return _make


acme = _fixture("acme.example")
other = _fixture("other.example")


def scoped(app_engine, tenant_id: UUID):
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    class _Scoped:
        async def __aenter__(self):
            self._session = factory()
            await self._session.__aenter__()
            self._transaction = self._session.begin()
            await self._transaction.__aenter__()
            await self._session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            return self._session

        async def __aexit__(self, *exc):
            await self._session.rollback()
            await self._session.__aexit__(None, None, None)

    return _Scoped()


def service(session, ids: dict[str, UUID], role: Role = Role.OWNER) -> GitHubConnectorService:
    return GitHubConnectorService(
        session,
        TenantContext(
            tenant_id=ids["tenant_id"],
            actor_id=ids["actor_id"],
            role=role,
            trace_id="integration",
        ),
    )


def store(session) -> DatabaseEnvelopeSecretStore:
    return DatabaseEnvelopeSecretStore(session, ENCRYPTION_KEY, "test-v1")


async def connect(session, ids, probe: FakeProbe, **overrides):
    return await service(session, ids).connect_personal_access_token(
        ids["site_id"],
        overrides.get("repository", "MasoodZaf/mindTools"),
        overrides.get("base_branch", "main"),
        overrides.get("path_template", "{path}.html"),
        overrides.get("token", "ghp_" + "a" * 36),
        probe,
        store(session),
    )


async def test_a_site_without_a_connector_is_refused_rather_than_defaulted(
    app_engine, acme
) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        with pytest.raises(HTTPException) as raised:
            await service(session, acme).resolve(
                acme["site_id"],
                # A repository and token are configured process-wide, and are
                # deliberately not enough: without the opt-in they are not a
                # credential this site is entitled to.
                settings(github_repository="MasoodZaf/mindTools", github_token="t" * 40),
                unused(),
                store(session),
            )

    assert raised.value.status_code == 409
    assert raised.value.detail == "deployment_connector_not_configured"


async def test_the_install_wide_token_is_recorded_every_time_it_is_used(
    app_engine, acme
) -> None:
    """The bridge for a running pilot, and it says so in the audit log.

    One token for every tenant cannot be made safe, only temporary. Recording
    each use means the day a second tenant exists there is a list of exactly
    which sites were still on it, rather than an assumption that none were.
    """
    async with scoped(app_engine, acme["tenant_id"]) as session:
        credential = await service(session, acme).resolve(
            acme["site_id"],
            settings(
                github_legacy_token_enabled=True,
                github_repository="MasoodZaf/mindTools",
                github_token="t" * 40,
            ),
            unused(),
            store(session),
        )
        assert credential.source == "legacy_settings"
        assert credential.connector_id is None
        assert credential.target.slug == "MasoodZaf/mindTools"

        await session.flush()
        recorded = (
            await session.execute(
                text(
                    "SELECT metadata->>'reason' AS reason FROM audit_event"
                    " WHERE tenant_id=:t AND action='connector.legacy_token_used'"
                ),
                {"t": acme["tenant_id"]},
            )
        ).all()
        assert [row.reason for row in recorded] == ["no_site_connector"]


async def test_a_connected_repository_supplies_the_credential_and_the_layout(
    app_engine, acme
) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        probe = writable()
        connector = await connect(
            session,
            acme,
            probe,
            base_branch="trunk",
            path_template="CalcHive/{path}.html",
            token="ghp_site_specific_token_aaaaaaaaaaaa",
        )
        assert connector.status == "active"
        assert connector.external_account_ref == "MasoodZaf/mindTools"
        assert connector.config_json == {
            "base_branch": "trunk",
            "path_template": "CalcHive/{path}.html",
        }
        # The token was tried before it was believed.
        assert probe.seen == [
            ("MasoodZaf/mindTools", "ghp_site_specific_token_aaaaaaaaaaaa")
        ]

        credential = await service(session, acme).resolve(
            acme["site_id"],
            # Process-wide settings point somewhere else entirely. The
            # connector must win, or nothing has actually changed.
            settings(
                github_legacy_token_enabled=True,
                github_repository="someone/else",
                github_token="wrong-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                github_path_template="{path}",
            ),
            unused(),
            store(session),
        )

    assert credential.token == "ghp_site_specific_token_aaaaaaaaaaaa"
    assert credential.target.slug == "MasoodZaf/mindTools"
    assert credential.target.base_branch == "trunk"
    assert credential.path_template == "CalcHive/{path}.html"
    assert credential.source == "github_pat"


async def test_the_stored_token_is_never_written_in_the_clear(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        await connect(session, acme, writable(), token="ghp_plaintext_would_show_here_aaaa")
        await session.flush()
        rows = (
            await session.execute(
                text("SELECT ciphertext FROM connector_secret WHERE tenant_id=:t"),
                {"t": acme["tenant_id"]},
            )
        ).scalars().all()

    assert len(rows) == 1
    assert b"ghp_plaintext_would_show_here_aaaa" not in bytes(rows[0])


async def test_two_tenants_deploy_with_two_different_credentials(
    app_engine, acme, other
) -> None:
    """The property the install-wide token made impossible.

    Both sites connect the same repository name on purpose: if resolution were
    keyed on anything but the site's own row, this would still pass with one
    token, and the assertion on the token value is what catches it.
    """
    async with scoped(app_engine, acme["tenant_id"]) as session:
        await connect(session, acme, writable(), token="ghp_acme_token_aaaaaaaaaaaaaaaaaaaa")
        first = await service(session, acme).resolve(
            acme["site_id"], settings(), unused(), store(session)
        )
        assert first.token == "ghp_acme_token_aaaaaaaaaaaaaaaaaaaa"

    async with scoped(app_engine, other["tenant_id"]) as session:
        await connect(session, other, writable(), token="ghp_other_token_bbbbbbbbbbbbbbbbbb")
        second = await service(session, other).resolve(
            other["site_id"], settings(), unused(), store(session)
        )
        assert second.token == "ghp_other_token_bbbbbbbbbbbbbbbbbb"

    assert first.token != second.token
    assert first.connector_id != second.connector_id


async def test_one_tenant_cannot_resolve_another_tenants_site(
    app_engine, acme, other
) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        await connect(session, acme, writable(), token="ghp_acme_token_aaaaaaaaaaaaaaaaaaaa")

    async with scoped(app_engine, other["tenant_id"]) as session:
        for config in (
            settings(),
            # With the fallback enabled too. Row-level security hides the other
            # tenant's connector, which is indistinguishable from having none --
            # so without an explicit site check this would quietly hand back the
            # install-wide token for a site the caller does not own.
            settings(
                github_legacy_token_enabled=True,
                github_repository="MasoodZaf/mindTools",
                github_token="t" * 40,
            ),
        ):
            with pytest.raises(HTTPException) as raised:
                await service(session, other).resolve(
                    acme["site_id"], config, unused(), store(session)
                )
            assert raised.value.status_code == 404
            assert raised.value.detail == "site_not_found"


async def test_a_read_only_token_is_refused_and_nothing_is_stored(app_engine, acme) -> None:
    read_only = FakeProbe(
        RepositoryFacts(
            full_name="MasoodZaf/mindTools",
            default_branch="main",
            can_push=False,
            archived=False,
        )
    )
    async with scoped(app_engine, acme["tenant_id"]) as session:
        with pytest.raises(HTTPException) as raised:
            await connect(session, acme, read_only)
        assert raised.value.detail == "github_token_cannot_write"

        await session.rollback()

    async with scoped(app_engine, acme["tenant_id"]) as session:
        stored = (
            await session.execute(
                text("SELECT count(*) FROM connector_secret WHERE tenant_id=:t"),
                {"t": acme["tenant_id"]},
            )
        ).scalar_one()
    assert stored == 0


async def test_an_unverified_site_cannot_gain_a_write_connector(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        await session.execute(
            text("UPDATE site SET verified_at=NULL WHERE id=:id"), {"id": acme["site_id"]}
        )
        with pytest.raises(HTTPException) as raised:
            await connect(session, acme, writable())

    assert raised.value.status_code == 409
    assert raised.value.detail == "site_not_verified"


async def test_a_member_without_authority_cannot_bind_a_repository(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        with pytest.raises(HTTPException) as raised:
            await service(session, acme, Role.VIEWER).connect_personal_access_token(
                acme["site_id"],
                "MasoodZaf/mindTools",
                "main",
                "{path}.html",
                "ghp_" + "a" * 36,
                writable(),
                store(session),
            )

    assert raised.value.status_code == 403


async def _seed_installation(session, ids: dict[str, UUID], installation_id: int) -> UUID:
    connector_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO connector(id,tenant_id,site_id,type,provider_key,status,"
            "external_account_ref,config_json,granted_scopes,version)"
            " VALUES(:id,:tenant_id,:site_id,:type,:provider,'active',"
            "'MasoodZaf/mindTools',CAST(:config AS jsonb),ARRAY['contents:write'],1)"
        ),
        {
            "id": connector_id,
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "type": GITHUB_CONNECTOR,
            "provider": PROVIDER_APP,
            "config": (
                '{"base_branch":"main","path_template":"CalcHive/{path}.html",'
                f'"installation_id":{installation_id}}}'
            ),
        },
    )
    return connector_id


async def test_an_installation_mints_a_token_instead_of_storing_one(app_engine, acme) -> None:
    """The point of the app: no credential at rest for this tenant at all."""
    async with scoped(app_engine, acme["tenant_id"]) as session:
        connector_id = await _seed_installation(session, acme, 4242)

        credential = await service(session, acme).resolve(
            acme["site_id"],
            app_settings(),
            minting("ghs_minted_for_this_deploy", ["MasoodZaf/mindTools"]),
            store(session),
        )

        secrets_held = (
            await session.execute(
                text("SELECT count(*) FROM connector_secret WHERE connector_id=:c"),
                {"c": connector_id},
            )
        ).scalar_one()

    assert credential.token == "ghs_minted_for_this_deploy"
    assert credential.source == "github_app"
    assert credential.path_template == "CalcHive/{path}.html"
    assert secrets_held == 0


async def test_a_repository_taken_back_out_of_the_installation_stops_deploying(
    app_engine, acme
) -> None:
    """The grant is the tenant's to withdraw, and withdrawing it must bite.

    GitHub still mints a token for the installation; it is simply scoped to
    what is left. Using it anyway would push to a repository whose owner had
    already revoked this app's access to the one it names.
    """
    async with scoped(app_engine, acme["tenant_id"]) as session:
        await _seed_installation(session, acme, 4242)

        with pytest.raises(HTTPException) as raised:
            await service(session, acme).resolve(
                acme["site_id"],
                app_settings(),
                minting("ghs_scoped_elsewhere", ["MasoodZaf/somethingElse"]),
                store(session),
            )

    assert raised.value.status_code == 409
    assert raised.value.detail == "github_repository_not_installed"


async def test_an_installation_is_useless_without_the_apps_own_key(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        await _seed_installation(session, acme, 4242)

        with pytest.raises(HTTPException) as raised:
            await service(session, acme).resolve(
                acme["site_id"], settings(), unused(), store(session)
            )

    assert raised.value.detail == "github_app_not_configured"


async def test_a_sign_in_is_issued_with_a_single_use_state(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        url, expires_at = await service(session, acme).begin_sign_in(
            acme["site_id"], app_settings()
        )
        await session.flush()
        states = (
            await session.execute(
                text(
                    "SELECT requested_scopes, requested_property_ref, created_by"
                    " FROM connector_oauth_state WHERE tenant_id=:t"
                ),
                {"t": acme["tenant_id"]},
            )
        ).all()
        connector = (
            await session.execute(
                text("SELECT status, config_json FROM connector WHERE tenant_id=:t"),
                {"t": acme["tenant_id"]},
            )
        ).one()

    query = parse_qs(urlsplit(url).query)
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert query["client_id"] == ["Iv1.testclient"]
    assert query["redirect_uri"] == ["http://localhost:8000/v1/connectors/github/callback"]
    assert len(query["state"][0]) >= 32
    # Nothing is typed, so nothing is waiting on the state but who asked.
    assert states[0].requested_scopes == ["github:sign_in"]
    assert states[0].requested_property_ref is None
    assert states[0].created_by == acme["actor_id"]
    assert connector.status == "pending_authorization"
    assert expires_at > datetime.now(UTC)


async def test_add_a_repository_goes_to_the_install_page(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        url, _ = await service(session, acme).begin_sign_in(
            acme["site_id"], app_settings(), install=True
        )
    assert url.startswith("https://github.com/apps/seo-autopilot/installations/new?state=")


async def test_an_app_without_its_sign_in_half_is_refused(app_engine, acme) -> None:
    """Without sign-in the only evidence left is an installation id in a URL."""
    async with scoped(app_engine, acme["tenant_id"]) as session:
        with pytest.raises(HTTPException) as raised:
            await service(session, acme).begin_sign_in(
                acme["site_id"],
                app_settings(github_app_client_id=None, github_app_client_secret=None),
            )
    assert raised.value.detail == "github_app_sign_in_not_configured"
