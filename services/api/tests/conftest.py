"""Shared fixtures, including the PostgreSQL-backed integration harness.

Service unit tests drive an `AsyncMock` session, which cannot observe how a real
session behaves -- autoflush ordering, constraint enforcement, or row-level
security. Tests that need those facts use the fixtures here and run against a
real database.

Two roles matter. Migrations run as the owning superuser, as they do in
deployment. Anything asserting tenant behaviour runs as `seo_autopilot_app`, the
NOSUPERUSER NOBYPASSRLS role the services use, because row-level security is
only observable from a role that cannot bypass it.

The suite is skipped when `TEST_DATABASE_URL` is unset so a bare `pytest` still
works without Docker. `make test-integration` sets it, and `make check` runs it,
so the coverage cannot quietly lapse.
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

MIGRATIONS = sorted((Path(__file__).resolve().parents[3] / "infra" / "migrations").glob("*.sql"))

# Local-only, and only ever set on the ephemeral test database.
APP_ROLE = "seo_autopilot_app"
APP_ROLE_PASSWORD = "integration-test-only"

requires_database = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is unset; run `make test-integration`",
)

_schema_ready = False


@pytest.fixture
def database_url() -> str:
    """Owner connection: runs migrations and makes schema-level assertions."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is unset")
    return url


@pytest.fixture
def app_database_url(database_url: str) -> str:
    """Application connection: the role the services use, which cannot bypass RLS."""
    _, _, tail = database_url.partition("://")
    _, _, host_and_db = tail.partition("@")
    return f"postgresql+asyncpg://{APP_ROLE}:{APP_ROLE_PASSWORD}@{host_and_db}"


async def _apply_migrations(database_url: str) -> None:
    """Rebuild the schema from the migration files.

    Each file holds several statements and its own BEGIN/COMMIT, which asyncpg's
    extended query protocol rejects, so they run over the raw driver connection
    where the simple protocol accepts a whole script.
    """
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        for migration in MIGRATIONS:
            await connection.execute(migration.read_text())
        # Migration 0027 creates the role without a password; granting it a
        # secret is an operational step, and this is the test environment's.
        await connection.execute(
            f"ALTER ROLE {APP_ROLE} WITH LOGIN PASSWORD '{APP_ROLE_PASSWORD}'"
        )
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def engine(database_url: str):
    global _schema_ready
    if not _schema_ready:
        await _apply_migrations(database_url)
        _schema_ready = True
    engine = create_async_engine(database_url)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def app_engine(engine, app_database_url: str):
    """Depends on `engine` so the schema and role exist before connecting."""
    app_engine = create_async_engine(app_database_url)
    yield app_engine
    await app_engine.dispose()


@pytest_asyncio.fixture
async def raw_session(engine) -> AsyncIterator[AsyncSession]:
    """Owner session with no tenant scope, for schema-level assertions."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def tenant_session_factory(app_engine):
    """Builds an application-role session scoped to a tenant, as the API does.

    Each session runs in one transaction that the fixture rolls back, so cases
    cannot leak rows into each other.
    """
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    def build(tenant_id: UUID):
        class _Scoped:
            async def __aenter__(self) -> AsyncSession:
                self._session = factory()
                await self._session.__aenter__()
                self._transaction = self._session.begin()
                await self._transaction.__aenter__()
                await self._session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(tenant_id)},
                )
                return self._session

            async def __aexit__(self, *exc) -> None:
                await self._session.rollback()
                await self._session.__aexit__(None, None, None)

        return _Scoped()

    return build


@pytest_asyncio.fixture
async def seeded_tenants(engine) -> AsyncIterator[tuple[UUID, UUID]]:
    """Two committed tenants, each owning one site, removed afterwards.

    Isolation cannot be proved inside a single rolled-back transaction: the rows
    the other tenant must not see have to be visible to a separate connection.
    Seeding runs as the owner because the fixture is scaffolding, not the
    behaviour under test.
    """
    tenant_a, tenant_b = uuid4(), uuid4()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        for tenant_id, slug in ((tenant_a, "tenant-a"), (tenant_b, "tenant-b")):
            await session.execute(
                text(
                    "INSERT INTO tenant (id, slug, name, status)"
                    " VALUES (:id, :slug, :name, 'active')"
                ),
                {"id": tenant_id, "slug": f"{slug}-{tenant_id.hex[:8]}", "name": slug},
            )
        for tenant_id, host in ((tenant_a, "a.example"), (tenant_b, "b.example")):
            await session.execute(
                text(
                    "INSERT INTO site"
                    " (tenant_id, name, canonical_origin, normalized_host, mode, status)"
                    " VALUES (:tenant_id, :host, :origin, :host, 'observe', 'active')"
                ),
                {"tenant_id": tenant_id, "host": host, "origin": f"https://{host}"},
            )
    yield tenant_a, tenant_b
    async with factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM site WHERE tenant_id = ANY(:ids)"), {"ids": [tenant_a, tenant_b]}
        )
        await session.execute(
            text("DELETE FROM tenant WHERE id = ANY(:ids)"), {"ids": [tenant_a, tenant_b]}
        )
