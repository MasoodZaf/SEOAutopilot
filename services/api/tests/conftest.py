"""Shared fixtures, including the PostgreSQL-backed integration harness.

Service unit tests drive an `AsyncMock` session, which cannot observe how a
real session behaves — autoflush ordering, constraint enforcement, or row-level
security. Tests that need those facts use the `tenant_session` fixture here and
run against a real database.

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

requires_database = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is unset; run `make test-integration`",
)


_schema_ready = False


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is unset")
    return url


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
async def raw_session(engine) -> AsyncIterator[AsyncSession]:
    """A session with no tenant scope set, for schema-level assertions."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def tenant_session_factory(engine):
    """Builds a session scoped to a tenant the way `get_tenant_session` does.

    Each session runs in one transaction that the test rolls back, so cases
    cannot leak rows into each other.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)

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
    """Two committed tenants, each owning one site, then removed afterwards.

    Isolation cannot be proved inside a single rolled-back transaction: the rows
    the other tenant must not see have to be visible to a separate connection.
    """
    tenant_a, tenant_b = uuid4(), uuid4()
    site_a, site_b = uuid4(), uuid4()
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
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_a)}
        )
        await session.execute(
            text(
                "INSERT INTO site (id, tenant_id, name, canonical_origin, normalized_host, mode, status)"
                " VALUES (:id, :tenant_id, 'A site', 'https://a.example', 'a.example', 'observe', 'active')"
            ),
            {"id": site_a, "tenant_id": tenant_a},
        )
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_b)}
        )
        await session.execute(
            text(
                "INSERT INTO site (id, tenant_id, name, canonical_origin, normalized_host, mode, status)"
                " VALUES (:id, :tenant_id, 'B site', 'https://b.example', 'b.example', 'observe', 'active')"
            ),
            {"id": site_b, "tenant_id": tenant_b},
        )
    yield tenant_a, tenant_b
    async with factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM site WHERE tenant_id = ANY(:ids)"), {"ids": [tenant_a, tenant_b]}
        )
        await session.execute(
            text("DELETE FROM tenant WHERE id = ANY(:ids)"), {"ids": [tenant_a, tenant_b]}
        )
