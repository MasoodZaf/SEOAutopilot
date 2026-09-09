"""Two people signing themselves up, and staying out of each other's data.

The system has only ever run one tenant, so isolation between two live ones has
been a property of the schema rather than an observed fact. These run against
PostgreSQL as the application role -- the one that cannot bypass row-level
security -- because none of this is visible from a role that can.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.context import ActorContext
from app.services.tenant_credentials import (
    GOOGLE_OAUTH_CLIENT,
    TenantCredentialStore,
    credential_aad,
)
from app.services.tenant_provisioning import (
    MAX_OWNED_TENANTS,
    TenantProvisioningService,
    slugify,
)
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

KEY = b"0123456789abcdef0123456789abcdef"


def _actor(user_id: UUID) -> ActorContext:
    return ActorContext(
        actor_id=user_id, email="t@example.com", display_name="Tester", trace_id="trace"
    )


async def _make_user(session, email: str) -> UUID:
    user_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO app_user (id, issuer, subject, email, email_normalized, status)"
            " VALUES (:id, 'https://accounts.google.com', :subject, :email, :email, 'active')"
        ),
        {"id": user_id, "subject": f"sub-{user_id.hex}", "email": email},
    )
    return user_id


@pytest.fixture
def authenticating_factory(app_engine):
    """An application-role session with the authenticating GUC set, as signup runs."""
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    def build():
        class _Scoped:
            async def __aenter__(self):
                self._session = factory()
                await self._session.__aenter__()
                self._transaction = self._session.begin()
                await self._transaction.__aenter__()
                await self._session.execute(
                    text("SELECT set_config('app.authenticating', 'on', true)")
                )
                return self._session

            async def __aexit__(self, *exc) -> None:
                await self._session.rollback()
                await self._session.__aexit__(None, None, None)

        return _Scoped()

    return build


async def test_signup_creates_a_tenant_and_makes_the_signer_its_owner(
    authenticating_factory,
) -> None:
    async with authenticating_factory() as session:
        user_id = await _make_user(session, "owner@example.com")
        tenant = await TenantProvisioningService(session).create(
            _actor(user_id), "Tester Workspace"
        )

        assert tenant.slug.startswith("tester-workspace")
        assert tenant.created_by == user_id
        role = await session.scalar(
            text(
                "SELECT role FROM tenant_membership"
                " WHERE tenant_id = :tenant AND user_id = :user AND status = 'active'"
            ),
            {"tenant": tenant.id, "user": user_id},
        )
        assert role == "owner"
        # Nobody invited them, and saying otherwise would put a grant in the
        # audit trail that never happened.
        invited_by = await session.scalar(
            text("SELECT invited_by FROM tenant_membership WHERE tenant_id = :tenant"),
            {"tenant": tenant.id},
        )
        assert invited_by is None
        action = await session.scalar(
            text("SELECT action FROM audit_event WHERE tenant_id = :tenant"),
            {"tenant": tenant.id},
        )
        assert action == "tenant.created"


async def test_two_people_may_choose_the_same_workspace_name(authenticating_factory) -> None:
    """A collision is ordinary, not an error. The second gets a suffix."""
    async with authenticating_factory() as session:
        first_user = await _make_user(session, "first@example.com")
        second_user = await _make_user(session, "second@example.com")
        service = TenantProvisioningService(session)
        first = await service.create(_actor(first_user), "Acme")
        second = await service.create(_actor(second_user), "Acme")

        assert first.slug != second.slug
        assert second.slug.startswith("acme-")


async def test_one_person_cannot_mint_unlimited_workspaces(authenticating_factory) -> None:
    async with authenticating_factory() as session:
        user_id = await _make_user(session, "prolific@example.com")
        service = TenantProvisioningService(session)
        for index in range(MAX_OWNED_TENANTS):
            await service.create(_actor(user_id), f"Workspace {index}")
        with pytest.raises(Exception) as refusal:
            await service.create(_actor(user_id), "One Too Many")
        assert "tenant_limit_reached" in str(refusal.value)


async def test_a_new_tenant_sees_none_of_an_existing_tenants_sites(
    authenticating_factory, tenant_session_factory, seeded_tenants
) -> None:
    """The property the whole exercise is for.

    A workspace created by somebody signing themselves up is scoped like every
    other tenant, so the two committed tenants seeded next door are invisible
    from inside it -- not filtered out by the application, but unreadable.
    """
    tenant_a, _tenant_b = seeded_tenants
    async with authenticating_factory() as session:
        user_id = await _make_user(session, "stranger@example.com")
        tenant = await TenantProvisioningService(session).create(_actor(user_id), "Stranger")
        new_tenant_id = tenant.id
        await session.commit()

    try:
        async with tenant_session_factory(new_tenant_id) as session:
            hosts = (await session.execute(text("SELECT normalized_host FROM site"))).scalars()
            assert list(hosts) == []

        # And the reverse: naming the new tenant from the old one's scope reads
        # nothing either.
        async with tenant_session_factory(tenant_a) as session:
            rows = (
                await session.execute(
                    text("SELECT id FROM tenant_membership WHERE tenant_id = :tenant"),
                    {"tenant": new_tenant_id},
                )
            ).scalars()
            assert list(rows) == []
    finally:
        async with tenant_session_factory(new_tenant_id) as cleanup:
            await cleanup.execute(
                text("DELETE FROM audit_event WHERE tenant_id = :t"), {"t": new_tenant_id}
            )


async def test_a_tenant_credential_is_unreadable_from_another_tenant(
    tenant_session_factory, seeded_tenants
) -> None:
    tenant_a, tenant_b = seeded_tenants
    async with tenant_session_factory(tenant_a) as session:
        store = TenantCredentialStore(session, KEY, "test-v1")
        await store.put(
            tenant_a,
            GOOGLE_OAUTH_CLIENT,
            config={"client_id": "a.apps.googleusercontent.com"},
            secret={"client_secret": "tenant-a-secret"},
            created_by=None,
        )
        # Readable by its owner.
        assert (await store.get(tenant_a, GOOGLE_OAUTH_CLIENT)) == {
            "client_secret": "tenant-a-secret"
        }
        rows = (await session.execute(text("SELECT count(*) FROM tenant_credential"))).scalar()
        assert rows == 1
        await session.commit()

    try:
        async with tenant_session_factory(tenant_b) as session:
            # Not filtered by the query -- invisible to it.
            rows = (
                await session.execute(text("SELECT count(*) FROM tenant_credential"))
            ).scalar()
            assert rows == 0
            store_b = TenantCredentialStore(session, KEY, "test-v1")
            assert await store_b.get(tenant_a, GOOGLE_OAUTH_CLIENT) is None
    finally:
        async with tenant_session_factory(tenant_a) as cleanup:
            await cleanup.execute(text("DELETE FROM tenant_credential"))
            await cleanup.commit()


async def test_a_credential_row_moved_between_tenants_does_not_decrypt(
    tenant_session_factory, seeded_tenants
) -> None:
    """The additional authenticated data is what makes moving a row useless.

    Row-level security stops the read. This is the second line: if a row is
    somehow written into another tenant, the ciphertext still will not open,
    because the tenant id is bound into what it was sealed against.
    """
    tenant_a, tenant_b = seeded_tenants
    async with tenant_session_factory(tenant_a) as session:
        store = TenantCredentialStore(session, KEY, "test-v1")
        credential = await store.put(
            tenant_a,
            GOOGLE_OAUTH_CLIENT,
            config={"client_id": "a.apps.googleusercontent.com"},
            secret={"client_secret": "tenant-a-secret"},
            created_by=None,
        )
        ciphertext, nonce = credential.ciphertext, credential.nonce

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    # Opening tenant A's ciphertext as if it belonged to tenant B.
    with pytest.raises(InvalidTag):
        AESGCM(KEY).decrypt(
            nonce, ciphertext, credential_aad(tenant_b, GOOGLE_OAUTH_CLIENT, "test-v1")
        )


async def test_one_live_credential_per_provider_and_rotation_supersedes(
    tenant_session_factory, seeded_tenants
) -> None:
    tenant_a, _ = seeded_tenants
    async with tenant_session_factory(tenant_a) as session:
        store = TenantCredentialStore(session, KEY, "test-v1")
        await store.put(
            tenant_a,
            GOOGLE_OAUTH_CLIENT,
            config={"client_id": "old.apps.googleusercontent.com"},
            secret={"client_secret": "old"},
            created_by=None,
        )
        await store.put(
            tenant_a,
            GOOGLE_OAUTH_CLIENT,
            config={"client_id": "new.apps.googleusercontent.com"},
            secret={"client_secret": "new"},
            created_by=None,
        )
        # The old one is revoked rather than deleted, so an authorization in
        # flight against it can still be explained.
        live = (
            await session.execute(
                text(
                    "SELECT count(*) FROM tenant_credential"
                    " WHERE tenant_id = :t AND revoked_at IS NULL"
                ),
                {"t": tenant_a},
            )
        ).scalar()
        total = (
            await session.execute(text("SELECT count(*) FROM tenant_credential"))
        ).scalar()
        assert (live, total) == (1, 2)
        assert (await store.get(tenant_a, GOOGLE_OAUTH_CLIENT)) == {"client_secret": "new"}


async def test_the_live_credential_index_refuses_a_second_active_row(
    tenant_session_factory, seeded_tenants
) -> None:
    """Two live clients would mean a consent started under one and finished
    under the other, which fails at the token exchange with nothing to act on."""
    tenant_a, _ = seeded_tenants
    async with tenant_session_factory(tenant_a) as session:
        insert = text(
            "INSERT INTO tenant_credential"
            " (tenant_id, provider, config_json, ciphertext, nonce, aad_hash, key_version)"
            " VALUES (:t, 'google_oauth_client', '{}'::jsonb, '\\x00', '\\x00', 'h', 'v')"
        )
        await session.execute(insert, {"t": tenant_a})
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.execute(insert, {"t": tenant_a})


def test_a_slug_is_derived_and_never_empty() -> None:
    assert slugify("Tester Workspace") == "tester-workspace"
    assert slugify("  ACME  Ltd.  ") == "acme-ltd"
    # A name with nothing slug-safe in it still has to produce something.
    assert slugify("!!!") == "workspace"
    assert slugify("日本語") == "workspace"
