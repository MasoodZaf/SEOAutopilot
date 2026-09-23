"""Requesting, editing and withdrawing a content draft, on the application role.

Drafting is bring-your-own-key: these check that a request is refused, not
queued, when the workspace has no key for the provider it asked for, and that
storing a key never makes it readable again.
"""

import base64
import secrets
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.services.content_drafts import ContentDraftService
from app.services.tenant_credentials import (
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    TenantCredentialService,
    store_for,
)
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

SETTINGS = Settings(
    connector_secret_backend="database_envelope",
    connector_secret_encryption_key=SecretStr(
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    ),
)


@pytest_asyncio.fixture
async def workspace(engine):
    ids = {name: uuid4() for name in ("tenant", "site", "run", "cluster", "brief", "refresh_cluster", "refresh", "page", "actor")}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        t, s = ids["tenant"], ids["site"]
        await session.execute(text("INSERT INTO tenant(id,slug,name,status) VALUES(:t,:slug,'w','active')"), {"t": t, "slug": f"w-{t.hex[:8]}"})
        await session.execute(
            text("INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,verified_at)"
                 " VALUES(:s,:t,'w','https://w.example','w.example','observe','active',now())"),
            {"s": s, "t": t},
        )
        await session.execute(
            text("INSERT INTO keyword_analysis_run(id,tenant_id,site_id,algorithm_version,window_start,window_end,content_hash)"
                 " VALUES(:r,:t,:s,'t','2026-09-01','2026-09-20',:h)"),
            {"r": ids["run"], "t": t, "s": s, "h": "a" * 64},
        )
        url = "https://w.example/"
        await session.execute(
            text("INSERT INTO page(id,tenant_id,site_id,normalized_url,url_hash) VALUES(:p,:t,:s,:u,:h)"),
            {"p": ids["page"], "t": t, "s": s, "u": url, "h": sha256(url.encode()).hexdigest()},
        )
        for cluster, brief, kind, key, page in (
            ("cluster", "brief", "new_page", "a", None),
            ("refresh_cluster", "refresh", "refresh", "b", ids["page"]),
        ):
            await session.execute(
                text("INSERT INTO keyword_cluster(id,tenant_id,site_id,analysis_run_id,label,cluster_key,intent,member_count,opportunity_score)"
                     " VALUES(:c,:t,:s,:r,:k,:k,'informational',1,30)"),
                {"c": ids[cluster], "t": t, "s": s, "r": ids["run"], "k": f"topic {key}"},
            )
            await session.execute(
                text("INSERT INTO content_brief(id,tenant_id,site_id,keyword_cluster_id,analysis_run_id,kind,cluster_label,intent,"
                     "priority_score,content_hash,target_page_id) VALUES(:b,:t,:s,:c,:r,:kind,:k,'informational',30,:h,:p)"),
                {"b": ids[brief], "t": t, "s": s, "c": ids[cluster], "r": ids["run"], "kind": kind, "k": f"topic {key}", "h": "b" * 64, "p": page},
            )
    yield ids
    async with factory() as session, session.begin():
        for table in ("outbox_event", "audit_event", "content_draft", "tenant_credential", "content_brief", "keyword_cluster", "keyword_analysis_run", "page", "site"):
            await session.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": ids["tenant"]})
        await session.execute(text("DELETE FROM tenant WHERE id=:t"), {"t": ids["tenant"]})


def ctx(ids: dict[str, UUID], role: Role = Role.OWNER) -> TenantContext:
    return TenantContext(tenant_id=ids["tenant"], actor_id=ids["actor"], role=role, trace_id="t")


async def test_a_request_without_the_workspace_key_is_refused(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        with pytest.raises(HTTPException) as refused:
            await ContentDraftService(session, ctx(workspace), SETTINGS).request(
                workspace["brief"], "key-00000001", "Asha", "anthropic"
            )
        assert refused.value.detail == "anthropic_key_not_configured"


async def test_with_a_key_a_draft_is_queued_once_with_its_event(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        store = store_for(session, SETTINGS)
        await TenantCredentialService(session, ctx(workspace)).upsert_ai_key(
            store, OPENAI_API_KEY, "sk-proj-abcdefghijklmnop1234"
        )
        service = ContentDraftService(session, ctx(workspace), SETTINGS)
        first = await service.request(workspace["brief"], "key-00000002", "Asha", "openai")
        again = await service.request(workspace["brief"], "key-00000002", "Asha", "openai")
        assert again.id == first.id
        assert (first.status, first.provider) == ("queued", "openai")
        events = await session.scalar(
            text("SELECT count(*) FROM outbox_event WHERE aggregate_id=:d AND event_type='content_draft.requested.v1'"),
            {"d": first.id},
        )
        assert events == 1
        # A second live draft for the same brief is a duplicate, not a second post.
        with pytest.raises(HTTPException) as duplicate:
            await service.request(workspace["brief"], "key-00000003", "Asha", "openai")
        assert duplicate.value.detail == "content_draft_already_live"


async def test_a_refresh_brief_cannot_be_drafted(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        with pytest.raises(HTTPException) as refused:
            await ContentDraftService(session, ctx(workspace), SETTINGS).request(
                workspace["refresh"], "key-00000004", "", "anthropic"
            )
        assert refused.value.detail == "content_brief_not_new_post"


async def test_edits_resolve_flags_and_a_stale_version_is_refused(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        store = store_for(session, SETTINGS)
        await TenantCredentialService(session, ctx(workspace)).upsert_ai_key(
            store, ANTHROPIC_API_KEY, "sk-ant-abcdefghijklmnop1234"
        )
        service = ContentDraftService(session, ctx(workspace), SETTINGS)
        draft = await service.request(workspace["brief"], "key-00000005", "Asha", "anthropic")
        # What the worker would have written.
        draft.status = "ready"
        draft.title, draft.slug, draft.body_markdown = "T", "t", "Body"
        draft.flags_json = [
            {"id": "claim-1", "kind": "claim", "text": "x", "resolved": False},
            {"id": "figure-2", "kind": "figure", "text": "y", "resolved": False},
        ]
        await session.flush()
        version = draft.version

        edited = await service.update(
            draft.id, expected_version=version, title="Better title", slug="better-title",
            meta_description=None, body_markdown=None, author_name=None,
            resolved_flags={"claim-1": "Verified against the RBI circular"},
        )
        assert edited.title == "Better title"
        resolved = {flag["id"]: flag["resolved"] for flag in edited.flags_json}
        assert resolved == {"claim-1": True, "figure-2": False}

        with pytest.raises(HTTPException) as stale:
            await service.update(
                draft.id, expected_version=version, title="Lost update", slug=None,
                meta_description=None, body_markdown=None, author_name=None, resolved_flags={},
            )
        assert stale.value.detail == "content_draft_stale"

        with pytest.raises(HTTPException) as bad_slug:
            await service.update(
                draft.id, expected_version=edited.version, title=None, slug="Not A Slug!",
                meta_description=None, body_markdown=None, author_name=None, resolved_flags={},
            )
        assert bad_slug.value.detail == "content_draft_slug_invalid"

        withdrawn = await service.withdraw(draft.id)
        assert withdrawn.status == "withdrawn"


async def test_viewers_cannot_request_and_keys_are_owner_only(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        with pytest.raises(HTTPException) as viewer:
            await ContentDraftService(session, ctx(workspace, Role.VIEWER), SETTINGS).request(
                workspace["brief"], "key-00000006", "", "anthropic"
            )
        assert viewer.value.status_code == 403
        store = store_for(session, SETTINGS)
        with pytest.raises(HTTPException):
            await TenantCredentialService(session, ctx(workspace, Role.EDITOR)).upsert_ai_key(
                store, ANTHROPIC_API_KEY, "sk-ant-abcdefghijklmnop1234"
            )
        # An OpenAI slot will not take an Anthropic key.
        with pytest.raises(HTTPException) as mismatched:
            await TenantCredentialService(session, ctx(workspace)).upsert_ai_key(
                store, OPENAI_API_KEY, "sk-ant-abcdefghijklmnop1234"
            )
        assert mismatched.value.detail == "openai_api_key_invalid"


async def test_a_stored_key_is_described_by_its_suffix_only(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        store = store_for(session, SETTINGS)
        await TenantCredentialService(session, ctx(workspace)).upsert_ai_key(
            store, ANTHROPIC_API_KEY, "sk-ant-abcdefghijklmnopWXYZ"
        )
        described = await store.describe(workspace["tenant"], ANTHROPIC_API_KEY)
        assert described is not None
        assert described.config_json == {"key_suffix": "WXYZ"}
        raw = await session.scalar(
            text("SELECT ciphertext FROM tenant_credential WHERE tenant_id=:t AND provider=:p"),
            {"t": workspace["tenant"], "p": ANTHROPIC_API_KEY},
        )
        assert b"sk-ant" not in bytes(raw)
