"""Every provider label the code writes must be one the database accepts.

`connector_secret.provider` has carried a CHECK constraint since migration 0009
listing exactly one value, `google_search_console`. The DNS provider connector
landed ten migrations later writing `dns_provider:cloudflare`, and nothing
widened the constraint -- so `connect_dns_provider` could not have stored a
secret against a real database at any point. It was never caught because the
connector's tests drive an `AsyncMock` session, where a CHECK constraint is not
something that exists.

This asserts the labels and the constraint against each other rather than
against a mock, so the next connector cannot repeat it.
"""

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.connector_secrets import DatabaseEnvelopeSecretStore
from app.services.connectors import ANALYTICS_CONNECTOR, DNS_PROVIDER_CONNECTOR, GSC_CONNECTOR
from app.services.github_connector import GITHUB_CONNECTOR, PROVIDER_PAT
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

ENCRYPTION_KEY = b"k" * 32

# The label each connector passes to `store`, written the way the calling code
# writes it rather than restated by hand.
PROVIDER_LABELS = [
    (GSC_CONNECTOR, GSC_CONNECTOR, None),
    (ANALYTICS_CONNECTOR, ANALYTICS_CONNECTOR, None),
    (DNS_PROVIDER_CONNECTOR, f"{DNS_PROVIDER_CONNECTOR}:cloudflare", "cloudflare"),
    (GITHUB_CONNECTOR, PROVIDER_PAT, PROVIDER_PAT),
]


@pytest_asyncio.fixture
async def tenant(engine):
    ids = {"tenant_id": uuid4(), "site_id": uuid4()}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'s','active')"),
            {"id": ids["tenant_id"], "slug": f"secret-{ids['tenant_id'].hex[:8]}"},
        )
        await session.execute(
            text(
                "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,"
                "status) VALUES(:id,:tenant_id,'s','https://s.example','s.example',"
                "'observe','active')"
            ),
            {"id": ids["site_id"], "tenant_id": ids["tenant_id"]},
        )
    yield ids
    async with factory() as session, session.begin():
        for table in ("connector_secret", "connector", "site"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": ids["tenant_id"]}
            )
        await session.execute(text("DELETE FROM tenant WHERE id=:id"), {"id": ids["tenant_id"]})


async def _connector(session, ids: dict[str, UUID], connector_type: str, key: str | None) -> UUID:
    connector_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO connector(id,tenant_id,site_id,type,provider_key,status)"
            " VALUES(:id,:tenant_id,:site_id,:type,:key,'pending_authorization')"
        ),
        {
            "id": connector_id,
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "type": connector_type,
            "key": key,
        },
    )
    return connector_id


async def test_each_connectors_secret_label_is_storable(engine, tenant) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for connector_type, label, provider_key in PROVIDER_LABELS:
        async with factory() as session, session.begin():
            connector_id = await _connector(session, tenant, connector_type, provider_key)
            store = DatabaseEnvelopeSecretStore(session, ENCRYPTION_KEY, "test-v1")

            secret_ref = await store.store(
                tenant["tenant_id"], connector_id, label, {"access_token": "value"}
            )
            assert secret_ref.startswith("db-envelope://")

            loaded = await store.load(tenant["tenant_id"], connector_id, secret_ref)
            assert loaded == {"access_token": "value"}


async def test_an_unrecognised_provider_label_is_still_refused(engine, tenant) -> None:
    """Widening the rule must not have removed it.

    The constraint is what stops a typo'd or attacker-chosen label from
    becoming a row nothing knows how to interpret, so the negative case is
    worth as much as the positive ones.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        connector_id = await _connector(session, tenant, GSC_CONNECTOR, None)
        with pytest.raises(Exception, match="connector_secret_provider_check"):
            await DatabaseEnvelopeSecretStore(session, ENCRYPTION_KEY, "test-v1").store(
                tenant["tenant_id"], connector_id, "something_else", {"access_token": "v"}
            )
