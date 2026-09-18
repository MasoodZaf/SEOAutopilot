"""One Google consent, every site connected: against PostgreSQL with RLS on.

The request from a working SEO tester was a single Google sign-in covering
Search Console and GA4. What makes that safe is not the consent screen but the
binding afterwards, so that is what is exercised here, through the same
unscoped-then-adopted callback session the real route uses:

- each site gets the property that actually covers its host, found rather than
  typed, and the best of several when the account can see more than one;
- a site whose properties the account cannot see is left exactly as it was;
- a working connector is never moved to a different property;
- a consent with one scope unticked binds only the service that was granted.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.services.connector_secrets import DatabaseEnvelopeSecretStore
from app.services.connectors import (
    ANALYTICS_CONNECTOR,
    GSC_CONNECTOR,
    GSC_READONLY_SCOPE,
    ConnectorOAuthCallbackService,
    ConnectorService,
)
from app.services.google_analytics import ANALYTICS_READONLY_SCOPE, AnalyticsProperty
from app.services.google_oauth import GoogleProperty, GoogleTokenGrant
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

KEY = b"k" * 32
BOTH = frozenset({GSC_READONLY_SCOPE, ANALYTICS_READONLY_SCOPE})


def settings() -> Settings:
    values: dict[str, object] = {
        "app_env": "development",
        "cursor_signing_key": "x" * 32,
        "google_connectors_enabled": True,
        "google_client_id": "client.apps.googleusercontent.com",
        "google_client_secret": "secret",
        "connector_secret_backend": "database_envelope",
        "connector_secret_encryption_key": "a" * 43 + "=",
    }
    return Settings(**values)  # type: ignore[arg-type]


@dataclass
class FakeGoogle:
    scopes: frozenset[str]
    properties: list[GoogleProperty]

    async def exchange_code(self, code: str) -> GoogleTokenGrant:
        return GoogleTokenGrant(
            access_token="access",
            refresh_token="refresh",
            scopes=self.scopes,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    async def list_properties(self, access_token: str) -> list[GoogleProperty]:
        return self.properties


@dataclass
class FakeAnalytics:
    properties: list[AnalyticsProperty]

    async def list_properties(self, access_token: str) -> list[AnalyticsProperty]:
        return self.properties


HOSTS = ("thecalchive.com", "codearc.net", "wordkitapp.com")

SEARCH = [
    GoogleProperty("sc-domain:thecalchive.com", "siteOwner"),
    # CodeArc has only a URL-prefix property, as it does in production.
    GoogleProperty("https://codearc.net/", "siteOwner"),
    # Two forms for WordKit: the domain property is the better one to read.
    GoogleProperty("https://wordkitapp.com/", "siteOwner"),
    GoogleProperty("sc-domain:wordkitapp.com", "siteFullUser"),
    # Visible, but at a level that cannot read metrics.
    GoogleProperty("sc-domain:other.example", "siteUnverifiedUser"),
]
ANALYTICS = [
    AnalyticsProperty("properties/546281601", "CodeArc", ("codearc.net",)),
    AnalyticsProperty("properties/999", "Unrelated", ("elsewhere.example",)),
]


@pytest_asyncio.fixture
async def workspace(engine):
    tenant_id, actor_id = uuid4(), uuid4()
    sites: dict[str, UUID] = {}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'ws','active')"),
            {"id": tenant_id, "slug": f"ws-{tenant_id.hex[:8]}"},
        )
        for index, host in enumerate(HOSTS):
            site_id = uuid4()
            sites[host] = site_id
            await session.execute(
                text(
                    "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,"
                    "status,verified_at,created_at) VALUES(:id,:t,:name,:origin,:host,'observe',"
                    "'active',:verified,:created)"
                ),
                {
                    "id": site_id,
                    "t": tenant_id,
                    "name": host,
                    "origin": f"https://{host}",
                    "host": host,
                    "verified": datetime(2026, 1, 1, tzinfo=UTC),
                    "created": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
                },
            )
    yield {"tenant_id": tenant_id, "actor_id": actor_id, "sites": sites}
    async with factory() as session, session.begin():
        for table in (
            "connector_secret",
            "connector_oauth_state",
            "connector",
            "outbox_event",
            "audit_event",
            "site",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": tenant_id}
            )
        await session.execute(text("DELETE FROM tenant WHERE id=:t"), {"t": tenant_id})


def context(ids) -> TenantContext:
    return TenantContext(
        tenant_id=ids["tenant_id"], actor_id=ids["actor_id"], role=Role.OWNER, trace_id="t"
    )


async def begin(app_engine, ids) -> str:
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(ids["tenant_id"])}
        )
        url, _ = await ConnectorService(session, context(ids)).begin_google_authorization(
            settings()
        )
    query = parse_qs(urlsplit(url).query)
    assert set(query["scope"][0].split()) == BOTH
    assert query["access_type"] == ["offline"]
    return query["state"][0]


async def callback(app_engine, state: str, google: FakeGoogle, analytics: FakeAnalytics):
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(text("SELECT set_config('app.oauth_callback', 'on', true)"))
        service = ConnectorOAuthCallbackService(
            session,
            google,
            DatabaseEnvelopeSecretStore(session, KEY, "test-v1"),
            analytics,
        )
        await service.complete_authorization(state, "code", "trace")
        return service.outcome


async def bindings(engine, ids) -> dict[tuple[str, str], tuple[str, str | None]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT s.normalized_host, c.type, c.status, c.external_account_ref"
                " FROM connector c JOIN site s ON s.id=c.site_id WHERE c.tenant_id=:t"
            ),
            {"t": ids["tenant_id"]},
        )
        return {(host, kind): (status, ref) for host, kind, status, ref in rows}


async def test_one_consent_connects_every_site_it_can(app_engine, engine, workspace) -> None:
    state = await begin(app_engine, workspace)
    outcome = await callback(
        app_engine, state, FakeGoogle(BOTH, SEARCH), FakeAnalytics(ANALYTICS)
    )

    found = await bindings(engine, workspace)
    assert found[("thecalchive.com", GSC_CONNECTOR)] == ("active", "sc-domain:thecalchive.com")
    assert found[("codearc.net", GSC_CONNECTOR)] == ("active", "https://codearc.net/")
    assert found[("wordkitapp.com", GSC_CONNECTOR)] == ("active", "sc-domain:wordkitapp.com")
    assert found[("codearc.net", ANALYTICS_CONNECTOR)] == ("active", "properties/546281601")
    # No GA4 property measures these two, so nothing is created for them.
    assert ("thecalchive.com", ANALYTICS_CONNECTOR) not in found
    assert ("wordkitapp.com", ANALYTICS_CONNECTOR) not in found

    assert outcome is not None
    assert len(outcome.linked) == 4
    assert sorted(outcome.unmatched) == [
        ("thecalchive.com", ANALYTICS_CONNECTOR),
        ("wordkitapp.com", ANALYTICS_CONNECTOR),
    ]


async def test_each_connector_holds_its_own_sealed_copy(app_engine, engine, workspace) -> None:
    state = await begin(app_engine, workspace)
    await callback(app_engine, state, FakeGoogle(BOTH, SEARCH), FakeAnalytics(ANALYTICS))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT c.id, c.secret_ref, count(s.id) FILTER (WHERE s.revoked_at IS NULL)"
                    " FROM connector c LEFT JOIN connector_secret s ON s.connector_id=c.id"
                    " WHERE c.tenant_id=:t AND c.status='active' GROUP BY c.id, c.secret_ref"
                ),
                {"t": workspace["tenant_id"]},
            )
        ).all()
    assert len(rows) == 4
    assert all(secret_ref and active == 1 for _, secret_ref, active in rows)
    assert len({secret_ref for _, secret_ref, _ in rows}) == 4


async def test_a_working_connector_is_never_moved_to_another_property(
    app_engine, engine, workspace
) -> None:
    first = await begin(app_engine, workspace)
    await callback(app_engine, first, FakeGoogle(BOTH, SEARCH), FakeAnalytics(ANALYTICS))

    # A second Google account that sees a different WordKit property and none
    # of the others.
    other_account = [GoogleProperty("https://wordkitapp.com/", "siteOwner")]
    second = await begin(app_engine, workspace)
    with pytest.raises(HTTPException) as refused:
        await callback(app_engine, second, FakeGoogle(BOTH, other_account), FakeAnalytics([]))
    assert refused.value.detail == "google_no_matching_properties"

    found = await bindings(engine, workspace)
    assert found[("wordkitapp.com", GSC_CONNECTOR)] == ("active", "sc-domain:wordkitapp.com")
    assert found[("thecalchive.com", GSC_CONNECTOR)] == ("active", "sc-domain:thecalchive.com")
    assert found[("codearc.net", ANALYTICS_CONNECTOR)] == ("active", "properties/546281601")


async def test_a_broken_connector_is_reconnected_to_the_property_it_read(
    app_engine, engine, workspace
) -> None:
    first = await begin(app_engine, workspace)
    await callback(app_engine, first, FakeGoogle(BOTH, SEARCH), FakeAnalytics(ANALYTICS))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        # WordKit had been reading the URL-prefix property when its grant died.
        await session.execute(
            text(
                "UPDATE connector SET status='reauthorization_required',"
                " external_account_ref='https://wordkitapp.com/'"
                " WHERE tenant_id=:t AND type='google_search_console'"
                " AND site_id=:s"
            ),
            {"t": workspace["tenant_id"], "s": workspace["sites"]["wordkitapp.com"]},
        )

    second = await begin(app_engine, workspace)
    await callback(app_engine, second, FakeGoogle(BOTH, SEARCH), FakeAnalytics(ANALYTICS))

    found = await bindings(engine, workspace)
    assert found[("wordkitapp.com", GSC_CONNECTOR)] == ("active", "https://wordkitapp.com/")


async def test_an_unticked_scope_binds_only_what_was_granted(
    app_engine, engine, workspace
) -> None:
    state = await begin(app_engine, workspace)
    outcome = await callback(
        app_engine,
        state,
        FakeGoogle(frozenset({GSC_READONLY_SCOPE}), SEARCH),
        FakeAnalytics(ANALYTICS),
    )

    found = await bindings(engine, workspace)
    assert ("codearc.net", ANALYTICS_CONNECTOR) not in found
    assert found[("codearc.net", GSC_CONNECTOR)][0] == "active"
    assert outcome is not None
    assert outcome.granted_scopes == (GSC_READONLY_SCOPE,)


async def test_an_account_that_sees_nothing_is_told_so(app_engine, engine, workspace) -> None:
    state = await begin(app_engine, workspace)
    with pytest.raises(HTTPException) as refused:
        await callback(app_engine, state, FakeGoogle(BOTH, []), FakeAnalytics([]))
    assert refused.value.detail == "google_no_matching_properties"


async def test_a_scope_this_product_never_asks_for_is_refused(
    app_engine, engine, workspace
) -> None:
    state = await begin(app_engine, workspace)
    wider = BOTH | {"https://www.googleapis.com/auth/webmasters"}
    with pytest.raises(HTTPException) as refused:
        await callback(app_engine, state, FakeGoogle(wider, SEARCH), FakeAnalytics(ANALYTICS))
    assert refused.value.detail == "oauth_scope_mismatch"
