"""Writes a queued content draft with a model.

Order matters here because of what can be held open. The row is claimed in its
own statement and committed; the model is called with no transaction open
(it can take minutes, well past the application role's idle-in-transaction
limit); the result is written in a second short transaction. A worker that dies
mid-call leaves a lease that expires, and the reaper requeues the row.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from redis.exceptions import ResponseError

from app.connectors.tenant_clients import credential_aad
from app.content_drafts.checks import DraftRejected, normalize, review_flags
from app.content_drafts.model import DraftModel, DraftModelError
from app.content_drafts.prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    DraftInput,
    SearchTerm,
    SitePage,
    build_user_prompt,
    input_hash,
)
from app.keywords.secrets import open_query
from app.tenancy import tenant_scope

STREAM = "seo-autopilot:events"
GROUP = "content-drafts"
EVENT = "content_draft.requested.v1"
# The credential each provider reads. Bring-your-own-key: no platform key.
CREDENTIALS = {"anthropic": "anthropic_api_key", "openai": "openai_api_key"}
MAX_ATTEMPTS = 3
logger = logging.getLogger(__name__)


class Pool(Protocol):
    def acquire(self) -> Any: ...


class Stream(Protocol):
    async def xgroup_create(self, name: str, groupname: str, **kwargs: Any) -> bool: ...
    async def xautoclaim(self, name: str, groupname: str, consumername: str, **kwargs: Any) -> Any: ...
    async def xreadgroup(self, groupname: str, consumername: str, streams: dict[str, str], **kwargs: Any) -> Any: ...
    async def xack(self, name: str, groupname: str, *ids: str) -> int: ...


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def tenant_key(
    connection: Any, tenant_id: UUID, credential: str, encryption_key: bytes
) -> str | None:
    row = await connection.fetchrow(
        """
        SELECT ciphertext,nonce,aad_hash,key_version FROM tenant_credential
        WHERE tenant_id=$1 AND provider=$2 AND revoked_at IS NULL
        """,
        tenant_id,
        credential,
    )
    if row is None:
        return None
    aad = credential_aad(tenant_id, credential, row["key_version"])
    if not hmac.compare_digest(hashlib.sha256(aad).hexdigest(), row["aad_hash"]):
        raise ValueError("tenant_credential_aad_mismatch")
    secret = json.loads(AESGCM(encryption_key).decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), aad))
    key = secret.get("api_key") if isinstance(secret, dict) else None
    return key if isinstance(key, str) and key else None


async def _fail(connection: Any, draft_id: UUID, tenant_id: UUID, code: str) -> None:
    await connection.execute(
        """
        UPDATE content_draft SET status='failed',finished_at=now(),lease_until=NULL,
          error_code=$3,updated_at=now()
        WHERE id=$1 AND tenant_id=$2 AND status='running'
        """,
        draft_id,
        tenant_id,
        code,
    )


async def _load_input(
    connection: Any, tenant_id: UUID, claimed: Mapping[str, Any], query_key: bytes
) -> DraftInput:
    brief = await connection.fetchrow(
        """
        SELECT b.cluster_label,b.intent,b.sections_json,b.query_hashes,b.kind,
               s.name AS site_name,s.canonical_origin
        FROM content_brief b JOIN site s ON s.id=b.site_id AND s.tenant_id=b.tenant_id
        WHERE b.id=$1 AND b.tenant_id=$2
        """,
        claimed["content_brief_id"],
        tenant_id,
    )
    if brief is None or brief["kind"] != "new_page":
        raise DraftRejected("content_brief_not_new_post")
    terms: list[SearchTerm] = []
    rows = await connection.fetch(
        """
        SELECT q.ciphertext,q.nonce,q.aad_hash,q.key_version,q.is_question,
               coalesce(m.impressions,0) AS impressions
        FROM search_query q
        LEFT JOIN LATERAL (
          SELECT max(impressions) AS impressions FROM keyword_cluster_member km
          WHERE km.tenant_id=q.tenant_id AND km.query_hash=q.query_hash
        ) m ON true
        WHERE q.tenant_id=$1 AND q.site_id=$2 AND q.query_hash=ANY($3::text[])
        ORDER BY impressions DESC LIMIT 40
        """,
        tenant_id,
        claimed["site_id"],
        list(brief["query_hashes"] or []),
    )
    for row in rows:
        try:
            term = open_query(
                query_key, tenant_id, claimed["site_id"], row["key_version"],
                bytes(row["nonce"]), bytes(row["ciphertext"]), row["aad_hash"],
            )
        except (InvalidTag, ValueError, UnicodeDecodeError):
            # A rotated key leaves older terms unreadable; draft without them.
            # The term itself is never logged.
            logger.info("search term unreadable", extra={"tenant_id": str(tenant_id)})
            continue
        terms.append(SearchTerm(term, float(row["impressions"] or 0), bool(row["is_question"])))

    pages = await connection.fetch(
        """
        SELECT DISTINCT ON (p.normalized_url) p.normalized_url,o.title
        FROM page_observation o
        JOIN page p ON p.id=o.page_id AND p.tenant_id=o.tenant_id
        WHERE o.tenant_id=$1 AND p.site_id=$2 AND o.http_status=200 AND o.title IS NOT NULL
          AND o.crawl_job_id=(
            SELECT id FROM crawl_job WHERE tenant_id=$1 AND site_id=$2
              AND status IN('completed','partial') ORDER BY created_at DESC LIMIT 1)
        ORDER BY p.normalized_url LIMIT 60
        """,
        tenant_id,
        claimed["site_id"],
    )
    origin = str(brief["canonical_origin"]).rstrip("/")
    site_pages = [
        SitePage(
            path=(str(row["normalized_url"])[len(origin):] or "/") if str(row["normalized_url"]).startswith(origin) else str(row["normalized_url"]),
            title=str(row["title"]),
        )
        for row in pages
    ]
    return DraftInput(
        site_name=str(brief["site_name"]),
        site_origin=origin,
        topic=str(brief["cluster_label"]),
        intent=str(brief["intent"]),
        sections=list(_json(brief["sections_json"]) or []),
        terms=terms,
        pages=site_pages,
        author_name=claimed["author_name"],
    )


async def process_draft(
    connection: Any,
    tenant_id: UUID,
    draft_id: UUID,
    *,
    models: Mapping[str, tuple[DraftModel, str]],
    encryption_key: bytes,
    monthly_budget_micros: int,
) -> None:
    """`models` maps a provider to (client, model name)."""
    claimed = await connection.fetchrow(
        """
        UPDATE content_draft SET status='running',attempts=attempts+1,
          lease_until=now()+interval '6 minutes',error_code=NULL,updated_at=now()
        WHERE id=$1 AND tenant_id=$2 AND attempts<$3
          AND (status='queued' OR (status='running' AND lease_until<now()))
        RETURNING site_id,content_brief_id,author_name,provider
        """,
        draft_id,
        tenant_id,
        MAX_ATTEMPTS,
    )
    if claimed is None:
        return

    now = datetime.now(UTC)
    spent = await connection.fetchval(
        "SELECT coalesce(sum(cost_micros),0) FROM content_draft WHERE tenant_id=$1 AND created_at>=$2",
        tenant_id,
        now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
    )
    if int(spent or 0) >= monthly_budget_micros:
        await _fail(connection, draft_id, tenant_id, "content_draft_budget_exhausted")
        return

    provider = str(claimed["provider"])
    if provider not in models or provider not in CREDENTIALS:
        await _fail(connection, draft_id, tenant_id, "provider_not_supported")
        return
    model, model_name = models[provider]
    api_key = await tenant_key(connection, tenant_id, CREDENTIALS[provider], encryption_key)
    if not api_key:
        await _fail(connection, draft_id, tenant_id, f"{provider}_key_not_configured")
        return

    try:
        data = await _load_input(connection, tenant_id, claimed, encryption_key)
    except DraftRejected as error:
        await _fail(connection, draft_id, tenant_id, str(error))
        return
    user = build_user_prompt(data)

    try:
        result = await model.draft(api_key=api_key, model=model_name, system=SYSTEM_PROMPT, user=user)
    except DraftModelError as error:
        if error.retryable:
            # Leave the row running: its lease expires, and the unacked
            # message is reclaimed after that (or the reaper requeues it),
            # which re-claims it here. `attempts` bounds the retries.
            raise
        await _fail(connection, draft_id, tenant_id, error.code)
        return

    try:
        draft = normalize(result.answer)
    except DraftRejected as error:
        # The call was made and billed even though its answer is unusable.
        await connection.execute(
            "UPDATE content_draft SET cost_micros=$3,input_tokens=$4,output_tokens=$5,model=$6 "
            "WHERE id=$1 AND tenant_id=$2",
            draft_id, tenant_id, result.cost_micros, result.input_tokens, result.output_tokens, result.model,
        )
        await _fail(connection, draft_id, tenant_id, str(error))
        return
    flags = review_flags(draft, data.pages)
    await connection.execute(
        """
        UPDATE content_draft SET status='ready',finished_at=now(),lease_until=NULL,error_code=NULL,
          model=$3,prompt_version=$4,input_hash=$5,input_tokens=$6,output_tokens=$7,
          cost_micros=$8,generated_json=$9::jsonb,
          title=$10,slug=$11,meta_description=$12,body_markdown=$13,flags_json=$14::jsonb,
          version=version+1,updated_at=now()
        WHERE id=$1 AND tenant_id=$2 AND status='running'
        """,
        draft_id,
        tenant_id,
        result.model,
        PROMPT_VERSION,
        input_hash(SYSTEM_PROMPT, user, model_name),
        result.input_tokens,
        result.output_tokens,
        result.cost_micros,
        json.dumps(draft),
        draft["title"],
        draft["slug"],
        draft["meta_description"],
        draft["body_markdown"],
        json.dumps(flags),
    )


def _messages(reply: object, reclaimed: bool = False) -> list[tuple[str, Mapping[str, str]]]:
    source = reply[1] if reclaimed and isinstance(reply, (list, tuple)) and len(reply) >= 2 else reply
    if not isinstance(source, list):
        return []
    if reclaimed:
        return [(mid, fields) for mid, fields in source if isinstance(mid, str) and isinstance(fields, Mapping)]
    result: list[tuple[str, Mapping[str, str]]] = []
    for stream in source:
        if isinstance(stream, (list, tuple)) and len(stream) == 2 and isinstance(stream[1], list):
            result.extend(
                (mid, fields) for mid, fields in stream[1] if isinstance(mid, str) and isinstance(fields, Mapping)
            )
    return result


async def run_content_draft_consumer(
    pool: Pool,
    streams: Stream,
    consumer: str,
    *,
    models: Mapping[str, tuple[DraftModel, str]],
    encryption_key: bytes,
    monthly_budget_micros: int,
) -> None:
    try:
        await streams.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise
    while True:
        reclaimed = _messages(
            await streams.xautoclaim(STREAM, GROUP, consumer, min_idle_time=420_000, start_id="0-0", count=1),
            True,
        )
        messages = reclaimed or _messages(
            await streams.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=1, block=5_000)
        )
        for message_id, fields in messages:
            if fields.get("type") != EVENT:
                await streams.xack(STREAM, GROUP, message_id)
                continue
            try:
                tenant_id = UUID(fields["tenant_id"])
                async with pool.acquire() as connection, tenant_scope(connection, tenant_id):
                    await process_draft(
                        connection,
                        tenant_id,
                        UUID(fields["aggregate_id"]),
                        models=models,
                        encryption_key=encryption_key,
                        monthly_budget_micros=monthly_budget_micros,
                    )
                await streams.xack(STREAM, GROUP, message_id)
            except (KeyError, ValueError):
                logger.exception("invalid content draft event", extra={"event_id": message_id})
                await streams.xack(STREAM, GROUP, message_id)
            except DraftModelError as error:
                # Retryable provider failure. Not acked: the message is
                # reclaimed once the row's lease has expired.
                logger.warning("content draft deferred", extra={"event_id": message_id, "code": error.code})
            except Exception:
                logger.exception("content draft failed", extra={"event_id": message_id})
