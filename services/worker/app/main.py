import asyncio
import logging
import os
from typing import cast

import asyncpg
import httpx
import redis.asyncio as redis

from app.analysis_consumer import AnalysisPool, AnalysisStream, run_analysis_consumer
from app.analytics.consumer import run_analytics_consumer
from app.connectors.google_oauth import GoogleTokenHttpRefresher, TokenRefresher
from app.connectors.grant_check import run_grant_checker
from app.connectors.runtime import (
    SyncStream,
    decode_encryption_key,
    require_secret_bytes,
)
from app.connectors.tenant_clients import TenantTokenRefresherFactory
from app.content_drafts.consumer import Stream as DraftStream
from app.content_drafts.consumer import run_content_draft_consumer
from app.content_drafts.model import AnthropicDraftModel, OpenAIDraftModel
from app.gsc.consumer import run_gsc_consumer
from app.notifications.deliver import run_notification_dispatcher
from app.outbox import DatabaseConnection, StreamClient, dispatch_batch
from app.pagespeed.client import PageSpeedClient
from app.pagespeed.consumer import Pool as PageSpeedPool
from app.pagespeed.consumer import Stream as PageSpeedStream
from app.pagespeed.consumer import run_pagespeed_consumer
from app.reaper import Pool as ReaperPool
from app.reaper import run_reaper
from app.routines.runner import Pool as RoutinePool
from app.routines.runner import Stream as RoutineStream
from app.routines.runner import run_routine_consumer
from app.routines.scheduler import Pool as SchedulerPool
from app.routines.scheduler import run_scheduler

logger = logging.getLogger(__name__)


async def run_dispatcher(pool: asyncpg.Pool, streams: redis.Redis) -> None:
    while True:
        try:
            async with pool.acquire() as connection:
                dispatched = await dispatch_batch(
                    cast(DatabaseConnection, connection),
                    cast(StreamClient, streams),
                )
            await asyncio.sleep(0.25 if dispatched else 2)
        except Exception:
            logger.exception("outbox dispatch failed")
            await asyncio.sleep(5)


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    # Provider URLs may contain API keys; never emit httpx request URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    database_url = os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    redis_url = os.environ["REDIS_URL"]
    connector_key = decode_encryption_key(os.environ["CONNECTOR_SECRET_ENCRYPTION_KEY"])
    query_hash_key = require_secret_bytes(
        os.environ.get("SEARCH_QUERY_HASH_KEY"), "search_query_hash_key_too_short"
    )
    # Two identities, because the worker does two different kinds of work.
    #
    # `pool` is the tenant-scoped application role. Every consumer that borrows
    # it declares whose data it is touching, and row-level security enforces it.
    #
    # `relay_pool` is for the sweeps that claim work across tenants before any
    # tenant scope exists -- the outbox dispatcher, the routine scheduler, the
    # notification dispatcher, and the lease reaper. They cannot run under a
    # scoped role, so they get an identity that is explicit about the exemption
    # instead of reaching for the superuser. It falls back to `pool` when unset
    # so a local stack without the second credential still starts.
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=4)
    relay_url = os.environ.get("RELAY_DATABASE_URL", "").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    relay_pool = (
        await asyncpg.create_pool(relay_url, min_size=1, max_size=2) if relay_url else pool
    )
    if relay_pool is pool:
        logger.warning(
            "RELAY_DATABASE_URL unset; cross-tenant sweeps share the application pool"
        )
    streams = redis.from_url(redis_url, decode_responses=True)
    pagespeed = PageSpeedClient(api_key=os.environ.get("PAGESPEED_API_KEY") or None)
    routines_enabled = os.environ.get("ROUTINES_ENABLED", "false").lower() == "true"
    notifications_enabled = os.environ.get("NOTIFICATIONS_ENABLED", "false").lower() == "true"
    app_base_url = os.environ.get("APP_BASE_URL", "http://localhost:3000")
    notification_client = httpx.AsyncClient(
        follow_redirects=False, timeout=httpx.Timeout(10.0)
    )
    # Redirects are never auto-followed: the guard validates a destination
    # before the request, so a redirect into private space would slip past it.
    # observe_competitor_page follows hops itself, revalidating each one.
    competitor_client = httpx.AsyncClient(
        follow_redirects=False, timeout=httpx.Timeout(15.0)
    )

    # A Google access token lives an hour, so a connector without a refresher
    # can only sync for an hour after somebody clicks consent. The client
    # credentials are the same pair the API uses for the consent exchange; both
    # services read one .env.local.
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_client = httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(15.0))
    token_refresher: TokenRefresher | None = None
    if google_client_id and google_client_secret:
        token_refresher = GoogleTokenHttpRefresher(
            refresh_client,
            client_id=google_client_id,
            client_secret=google_client_secret,
        )

    # Which client renews a grant is the tenant's, not the deployment's: a
    # refresh token is redeemable only by the client it was issued to, and
    # presenting it under another one comes back as `invalid_grant` -- which
    # reads here as a revoked grant, so the tenant is sent round the consent
    # screen for ever. The pair above is the fallback for tenants that never
    # supplied their own.
    refresher_factory = TenantTokenRefresherFactory(
        pool,
        refresh_client,
        encryption_key=connector_key,
        fallback=token_refresher,
    )
    if token_refresher is None:
        logger.warning(
            "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET are unset; a tenant that has not "
            "stored its own OAuth client cannot renew an access token, and its Search "
            "Console and GA4 syncs stop working one hour after consent"
        )

    background = [
        run_dispatcher(relay_pool, streams),
        # Nothing else re-reads the work tables, so without this a run whose
        # stream message was lost stays 'running' until someone notices.
        run_reaper(cast(ReaperPool, relay_pool)),
        run_analysis_consumer(
            cast(AnalysisPool, pool),
            cast(AnalysisStream, streams),
            f"worker-{os.getpid()}",
        ),
        run_gsc_consumer(
            pool,
            cast(SyncStream, streams),
            f"gsc-worker-{os.getpid()}",
            encryption_key=connector_key,
            query_hash_key=query_hash_key,
            query_key_version=os.environ.get("CONNECTOR_SECRET_KEY_VERSION", "local-v1"),
            refresher=token_refresher,
            refresher_factory=refresher_factory.for_tenant,
        ),
        # Reads the same stream as the Search Console consumer and claims only
        # `google_analytics` rows, so neither has to reject the other's work.
        run_analytics_consumer(
            pool,
            cast(SyncStream, streams),
            f"analytics-worker-{os.getpid()}",
            encryption_key=connector_key,
            key_version=os.environ.get("CONNECTOR_SECRET_KEY_VERSION", "local-v1"),
            refresher=token_refresher,
            refresher_factory=refresher_factory.for_tenant,
        ),
        # Renews every Google grant nothing has proven good for a day, so a
        # dead one reaches the connection manager the day it dies rather than
        # at the next scheduled sync -- or never, for a site without one.
        run_grant_checker(
            relay_pool,
            pool,
            encryption_key=connector_key,
            key_version=os.environ.get("CONNECTOR_SECRET_KEY_VERSION", "local-v1"),
            refresher_factory=refresher_factory.for_tenant,
        ),
        run_pagespeed_consumer(
            cast(PageSpeedPool, pool),
            cast(PageSpeedStream, streams),
            f"pagespeed-worker-{os.getpid()}",
            pagespeed,
        ),
        # AI blog drafts, the only model calls in the product. Each workspace
        # brings its own Anthropic or OpenAI key; the worker holds none.
        run_content_draft_consumer(
            pool,
            cast(DraftStream, streams),
            f"draft-worker-{os.getpid()}",
            models={
                "anthropic": (
                    AnthropicDraftModel(),
                    os.environ.get("CONTENT_DRAFT_ANTHROPIC_MODEL", "claude-opus-5"),
                ),
                "openai": (
                    OpenAIDraftModel(),
                    os.environ.get("CONTENT_DRAFT_OPENAI_MODEL", "gpt-5"),
                ),
            },
            encryption_key=connector_key,
            monthly_budget_micros=int(
                os.environ.get("CONTENT_DRAFT_MONTHLY_BUDGET_MICROS", "25000000")
            ),
        ),
    ]
    if routines_enabled:
        background.append(run_scheduler(cast(SchedulerPool, relay_pool)))
        background.append(
            run_routine_consumer(
                cast(RoutinePool, pool),
                cast(RoutineStream, streams),
                f"routine-worker-{os.getpid()}",
                connector_key,
                competitor_client,
            )
        )
    if notifications_enabled:
        background.append(
            run_notification_dispatcher(
                relay_pool, notification_client, connector_key, app_base_url
            )
        )

    try:
        await asyncio.gather(*background)
    finally:
        await competitor_client.aclose()
        await notification_client.aclose()
        await pagespeed.close()
        await streams.aclose()
        if relay_pool is not pool:
            await relay_pool.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
