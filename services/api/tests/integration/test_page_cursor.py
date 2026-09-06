"""Keyset pagination over page inventory, against PostgreSQL.

`tests/test_cursors.py` proves the token: it is signed, versioned, bound to a
site and rejected when tampered with. What it cannot show is whether paging
with that token returns each page exactly once, because that depends on how the
comparison behaves against real rows.

Ties are the normal case here, not an edge case. `page.last_seen_at` defaults to
`now()`, which is fixed for the whole transaction, so a crawl that upserts five
hundred pages gives every one of them the same timestamp. A keyset cursor that
ordered on the timestamp alone would then skip or repeat rows on every page
boundary, and the inventory would quietly lose pages -- a defect that looks like
a small site rather than a bug.
"""

from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.context import Role, TenantContext
from app.core.cursors import (
    CursorError,
    PageCursor,
    decode_page_cursor,
    encode_page_cursor,
)
from app.services.pages import PageService
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

PAGE_COUNT = 25
SIGNING_KEY = "integration-cursor-signing-key-32-chars"


@pytest_asyncio.fixture
async def inventory(engine):
    """One site whose pages all share a `last_seen_at`, as a real crawl leaves them."""
    ids = {name: uuid4() for name in ("tenant_id", "site_id", "actor_id")}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'cur','active')"),
            {"id": ids["tenant_id"], "slug": f"cur-{ids['tenant_id'].hex[:8]}"},
        )
        await session.execute(
            text(
                "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,"
                "mode,status,verified_at)"
                " VALUES(:id,:tenant_id,'c','https://cur.example','cur.example',"
                "'observe','active',now())"
            ),
            {"id": ids["site_id"], "tenant_id": ids["tenant_id"]},
        )
        for index in range(PAGE_COUNT):
            url = f"https://cur.example/page-{index:03d}"
            await session.execute(
                text(
                    "INSERT INTO page(tenant_id,site_id,normalized_url,url_hash)"
                    " VALUES(:tenant_id,:site_id,:url,:url_hash)"
                ),
                {
                    "tenant_id": ids["tenant_id"],
                    "site_id": ids["site_id"],
                    "url": url,
                    "url_hash": sha256(url.encode()).hexdigest(),
                },
            )
    yield ids
    async with factory() as session, session.begin():
        for table in ("page", "site"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id=:tenant_id"),
                {"tenant_id": ids["tenant_id"]},
            )
        await session.execute(
            text("DELETE FROM tenant WHERE id=:id"), {"id": ids["tenant_id"]}
        )


def pages_service(session, tenant_id: UUID) -> PageService:
    return PageService(
        session,
        TenantContext(
            tenant_id=tenant_id, actor_id=uuid4(), role=Role.OWNER, trace_id="integration"
        ),
    )


async def test_every_page_appears_exactly_once_across_the_walk(
    tenant_session_factory, inventory
) -> None:
    """The property that matters: no page lost, none served twice."""
    seen: list[str] = []
    cursor: PageCursor | None = None
    async with tenant_session_factory(inventory["tenant_id"]) as session:
        service = pages_service(session, inventory["tenant_id"])
        for _ in range(PAGE_COUNT):  # a bound, so a broken cursor cannot loop forever
            result = await service.list_pages(inventory["site_id"], 7, cursor)
            assert result is not None
            pages, cursor = result
            seen.extend(page.normalized_url for page in pages)
            if cursor is None:
                break

    assert cursor is None, "the walk did not terminate"
    assert len(seen) == PAGE_COUNT
    assert len(set(seen)) == PAGE_COUNT, "a page was served twice"
    assert set(seen) == {f"https://cur.example/page-{i:03d}" for i in range(PAGE_COUNT)}


async def test_all_pages_share_a_timestamp_so_the_tiebreak_is_doing_the_work(
    tenant_session_factory, inventory
) -> None:
    """Guards the test above from passing for the wrong reason.

    If the fixture ever produced distinct timestamps, the walk would succeed on
    the timestamp alone and prove nothing about the tiebreak.
    """
    async with tenant_session_factory(inventory["tenant_id"]) as session:
        distinct = (
            await session.execute(
                text("SELECT count(DISTINCT last_seen_at) FROM page WHERE site_id=:site_id"),
                {"site_id": inventory["site_id"]},
            )
        ).scalar_one()
        assert distinct == 1


async def test_the_final_page_reports_no_next_cursor(
    tenant_session_factory, inventory
) -> None:
    async with tenant_session_factory(inventory["tenant_id"]) as session:
        result = await pages_service(session, inventory["tenant_id"]).list_pages(
            inventory["site_id"], PAGE_COUNT, None
        )
        assert result is not None
        pages, cursor = result
        assert len(pages) == PAGE_COUNT
        assert cursor is None


async def test_another_tenant_cannot_replay_the_cursor(
    tenant_session_factory, inventory
) -> None:
    """The token is bound to a site, not to a tenant.

    A signed cursor is a bearer token: nothing in it says who it was issued to,
    so `decode_page_cursor` accepts it for anyone asking about that site. What
    has to refuse is the service, and underneath it row-level security. This is
    the case the pure cursor tests cannot reach.
    """
    async with tenant_session_factory(inventory["tenant_id"]) as session:
        result = await pages_service(session, inventory["tenant_id"]).list_pages(
            inventory["site_id"], 5, None
        )
        assert result is not None
        _, issued = result
        assert issued is not None

    token = encode_page_cursor(issued, SIGNING_KEY)
    # The token still verifies for the site it names -- the refusal is not here.
    assert decode_page_cursor(token, inventory["site_id"], SIGNING_KEY) == issued

    stranger = uuid4()
    async with tenant_session_factory(stranger) as session:
        assert (
            await pages_service(session, stranger).list_pages(
                inventory["site_id"], 5, issued
            )
        ) is None


async def test_a_cursor_from_another_site_is_refused_by_the_token_itself(
    tenant_session_factory, inventory
) -> None:
    """Belt to the service's braces: the site binding is checked before any query."""
    async with tenant_session_factory(inventory["tenant_id"]) as session:
        result = await pages_service(session, inventory["tenant_id"]).list_pages(
            inventory["site_id"], 5, None
        )
        assert result is not None
        _, issued = result
        assert issued is not None

    with pytest.raises(CursorError, match="cursor_scope_invalid"):
        decode_page_cursor(encode_page_cursor(issued, SIGNING_KEY), uuid4(), SIGNING_KEY)
