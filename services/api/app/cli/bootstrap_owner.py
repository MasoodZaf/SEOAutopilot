"""Invite the first owner of a tenant, from the host rather than the API.

Every membership comes from an invitation and every invitation comes from a
member, which leaves the first one with nowhere to come from. This is that one
case, and it is deliberately not an endpoint: an HTTP route that mints owners
would be a way into any tenant, guarded only by whatever is in front of it.

It refuses a tenant that already has an active owner. An operator with a
database credential can of course do anything; the point is that this tool is a
bootstrap and not a standing route to ownership, so using it that way has to be
a decision somebody makes explicitly with psql, where it is visible.

    python -m app.cli.bootstrap_owner --tenant-slug codearc-pilot --email ada@example.com

The invitation is claimed the first time that address signs in through the
identity provider with a verified email.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.services.membership import INVITATION_LIFETIME


class BootstrapError(RuntimeError):
    pass


async def bootstrap_owner(
    database_url: str,
    tenant_slug: str,
    email: str,
    *,
    lifetime: timedelta = INVITATION_LIFETIME,
) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized:
        raise BootstrapError("email_invalid")
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            tenant_id = await connection.scalar(
                text("SELECT id FROM tenant WHERE slug = :slug"), {"slug": tenant_slug}
            )
            if tenant_id is None:
                raise BootstrapError(f"no tenant with slug {tenant_slug!r}")
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            owners = await connection.scalar(
                text(
                    "SELECT count(*) FROM tenant_membership"
                    " WHERE tenant_id = :tenant_id AND role = 'owner' AND status = 'active'"
                ),
                {"tenant_id": tenant_id},
            )
            if owners:
                raise BootstrapError(
                    f"{tenant_slug} already has {owners} active owner(s); invite from the "
                    "application instead"
                )
            open_already = await connection.scalar(
                text(
                    "SELECT count(*) FROM tenant_invitation"
                    " WHERE tenant_id = :tenant_id AND email_normalized = :email"
                    " AND accepted_at IS NULL AND revoked_at IS NULL"
                ),
                {"tenant_id": tenant_id, "email": normalized},
            )
            if open_already:
                raise BootstrapError(f"{normalized} already has an open invitation")
            expires_at = datetime.now(UTC) + lifetime
            invitation_id = await connection.scalar(
                text(
                    "INSERT INTO tenant_invitation"
                    " (tenant_id, email_normalized, role, invited_by, expires_at)"
                    " VALUES (:tenant_id, :email, 'owner', NULL, :expires_at)"
                    " RETURNING id"
                ),
                {"tenant_id": tenant_id, "email": normalized, "expires_at": expires_at},
            )
            payload = {"email": normalized, "role": "owner", "tenant_slug": tenant_slug}
            await connection.execute(
                text(
                    "INSERT INTO audit_event"
                    " (tenant_id, actor_type, actor_id, action, resource_type,"
                    "  resource_id, trace_id, metadata, event_hash)"
                    " VALUES (:tenant_id, 'operator', NULL, 'membership.bootstrapped',"
                    "  'tenant_invitation', :resource_id, :trace_id, :metadata, :event_hash)"
                ),
                {
                    "tenant_id": tenant_id,
                    "resource_id": str(invitation_id),
                    "trace_id": f"bootstrap-{invitation_id}",
                    "metadata": json.dumps(payload, sort_keys=True),
                    "event_hash": _hash(payload),
                },
            )
            return (
                f"invited {normalized} as owner of {tenant_slug}; the invitation is claimed "
                f"on first sign-in and expires {expires_at.isoformat()}"
            )
    finally:
        await engine.dispose()


def _hash(payload: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--lifetime-days",
        type=int,
        default=INVITATION_LIFETIME.days,
        help="How long the invitation stays claimable.",
    )
    arguments = parser.parse_args()
    try:
        print(
            asyncio.run(
                bootstrap_owner(
                    get_settings().database_url,
                    arguments.tenant_slug,
                    arguments.email,
                    lifetime=timedelta(days=arguments.lifetime_days),
                )
            )
        )
    except BootstrapError as error:
        print(f"refused: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
