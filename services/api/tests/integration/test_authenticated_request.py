"""A real token, through the real app, to a real route.

Everything else about identity is tested in pieces: the verifier against crafted
tokens, membership resolution against PostgreSQL. Pieces passing is what this
repository has been burned by before -- a green suite and a request that does not
work -- and the wiring between them is where that gap lives. Nothing until now
has established that a token signed by a provider produces an authenticated
request through `app`, with the dependency graph, the session, and row-level
security all participating.

So this signs an RS256 token with a key served over HTTP from a JWKS endpoint the
verifier actually fetches, and issues real requests. What is faked is the
identity provider and nothing else.
"""

from __future__ import annotations

import base64
import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import auth as auth_module
from app.core.config import Settings, get_settings
from tests.conftest import requires_database

pytestmark = requires_database

ISSUER = "https://issuer.test"
AUDIENCE = "seo-autopilot"
KID = "e2e-key"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwks() -> bytes:
    numbers = _key.public_key().public_numbers()

    def number(value: int) -> str:
        return _b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))

    return json.dumps(
        {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": KID,
                    "use": "sig",
                    "alg": "RS256",
                    "n": number(numbers.n),
                    "e": number(numbers.e),
                }
            ]
        }
    ).encode()


class _JwksHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = _jwks()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence, so a passing test does not print a request log."""


@pytest.fixture(scope="module")
def jwks_server():
    """A real key-set endpoint, because the verifier really fetches one."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _JwksHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/jwks"
    server.shutdown()
    server.server_close()


def token(
    *,
    subject: str = "provider-subject-e2e",
    email: str = "ada@example.com",
    email_verified: bool = True,
    lifetime: timedelta = timedelta(minutes=10),
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": subject,
            "email": email,
            "email_verified": email_verified,
            "name": "Ada",
            "iat": int(now.timestamp()),
            "exp": int((now + lifetime).timestamp()),
        },
        _key,  # pyright: ignore[reportArgumentType]
        algorithm="RS256",
        headers={"kid": KID},
    )


@pytest_asyncio.fixture
async def seeded(engine):
    """Two tenants, a site in each, and one inviting user. Committed."""
    ids = {name: uuid4() for name in ("tenant_a", "tenant_b", "site_a", "site_b", "inviter")}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        for key, site_key, label in (
            ("tenant_a", "site_a", "e2e-a"),
            ("tenant_b", "site_b", "e2e-b"),
        ):
            await session.execute(
                text(
                    "INSERT INTO tenant(id,slug,name,status)"
                    " VALUES(:id,:slug,:slug,'active')"
                ),
                {"id": ids[key], "slug": f"{label}-{ids[key].hex[:8]}"},
            )
            await session.execute(
                text(
                    "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,status)"
                    " VALUES(:id,:tenant_id,:name,'https://example.com',:host,'active')"
                ),
                {
                    "id": ids[site_key],
                    "tenant_id": ids[key],
                    "name": label,
                    "host": f"{ids[site_key].hex[:8]}.example.com",
                },
            )
        await session.execute(
            text(
                "INSERT INTO app_user(id,issuer,subject,email,email_normalized,status)"
                " VALUES(:id,:issuer,:subject,:email,:email,'active')"
            ),
            {
                "id": ids["inviter"],
                "issuer": ISSUER,
                "subject": f"inviter-{ids['inviter'].hex[:8]}",
                "email": f"inviter-{ids['inviter'].hex[:8]}@example.com",
            },
        )
    yield ids
    async with factory() as session, session.begin():
        for table in (
            "tenant_membership",
            "tenant_invitation",
            "audit_event",
            "outbox_event",
            "site",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = ANY(:ids)"),
                {"ids": [ids["tenant_a"], ids["tenant_b"]]},
            )
        await session.execute(
            text("DELETE FROM app_user WHERE issuer = :issuer"), {"issuer": ISSUER}
        )
        await session.execute(
            text("DELETE FROM tenant WHERE id = ANY(:ids)"),
            {"ids": [ids["tenant_a"], ids["tenant_b"]]},
        )


async def invite(engine, tenant_id, inviter, email: str, role: str = "owner") -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenant_invitation"
                " (tenant_id,email_normalized,role,invited_by,expires_at)"
                " VALUES(:tenant_id,:email,:role,:invited_by,:expires_at)"
            ),
            {
                "tenant_id": tenant_id,
                "email": email,
                "role": role,
                "invited_by": inviter,
                "expires_at": datetime.now(UTC) + timedelta(days=7),
            },
        )


@pytest.fixture
def client(monkeypatch, jwks_server, app_database_url):
    """The real app, pointed at the test database, with a provider configured.

    The session factory is redirected rather than the auth dependency
    overridden: overriding the dependency is exactly the thing that would make
    this test pass while the wiring is broken.
    """
    import app.db.session as session_module
    from app.main import app

    # NullPool, and one portal for the whole fixture. A pooled connection
    # belongs to the event loop that opened it, and TestClient runs each request
    # in its own loop unless it is entered as a context manager -- the second
    # request then finds a connection attached to a loop that has gone.
    engine = create_async_engine(app_database_url, poolclass=NullPool)
    monkeypatch.setattr(
        session_module, "session_factory", async_sessionmaker(engine, expire_on_commit=False)
    )

    configured = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="development",
        cursor_signing_key="c" * 32,
        oidc_issuer_url=ISSUER,
        oidc_audience=AUDIENCE,
        oidc_jwks_uri=jwks_server,
    )
    app.dependency_overrides[get_settings] = lambda: configured
    auth_module.reset_oidc_verifier()
    with TestClient(app) as connected:
        yield connected
    app.dependency_overrides.clear()
    auth_module.reset_oidc_verifier()


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


@pytest.mark.asyncio
async def test_a_signed_token_reaches_a_route_and_sees_only_its_tenant(
    client, engine, seeded
) -> None:
    """The whole path: JWKS fetch, verification, invitation, membership, RLS."""
    await invite(engine, seeded["tenant_a"], seeded["inviter"], "ada@example.com")

    response = client.get("/v1/sites", headers=bearer(token()))

    assert response.status_code == 200, response.text
    hosts = {item["normalized_host"] for item in response.json()["data"]}
    assert any(host.startswith(seeded["site_a"].hex[:8]) for host in hosts)
    # The other tenant's site exists and must not be in the answer.
    assert not any(host.startswith(seeded["site_b"].hex[:8]) for host in hosts)


@pytest.mark.asyncio
async def test_a_valid_token_with_no_invitation_is_refused_by_the_route(
    client, seeded
) -> None:
    response = client.get("/v1/sites", headers=bearer(token(subject="a-stranger")))
    assert response.status_code == 403
    assert response.json()["detail"] == "no_tenant_membership"


@pytest.mark.asyncio
async def test_an_expired_token_never_reaches_the_route(client, engine, seeded) -> None:
    await invite(engine, seeded["tenant_a"], seeded["inviter"], "ada@example.com")
    response = client.get(
        "/v1/sites", headers=bearer(token(lifetime=timedelta(minutes=-30)))
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "token_expired"


@pytest.mark.asyncio
async def test_a_token_from_the_wrong_key_never_reaches_the_route(
    client, engine, seeded
) -> None:
    await invite(engine, seeded["tenant_a"], seeded["inviter"], "ada@example.com")
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "provider-subject-e2e",
            "email": "ada@example.com",
            "email_verified": True,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        other,  # pyright: ignore[reportArgumentType]
        algorithm="RS256",
        headers={"kid": KID},
    )
    assert client.get("/v1/sites", headers=bearer(forged)).status_code == 401


@pytest.mark.asyncio
async def test_two_memberships_need_the_header_and_honour_it(
    client, engine, seeded
) -> None:
    """The selector is a real request header, not just a function parameter."""
    await invite(engine, seeded["tenant_a"], seeded["inviter"], "ada@example.com")
    await invite(engine, seeded["tenant_b"], seeded["inviter"], "ada@example.com")

    ambiguous = client.get("/v1/sites", headers=bearer(token()))
    assert ambiguous.status_code == 409
    assert ambiguous.json()["detail"] == "tenant_selection_required"

    chosen = client.get(
        "/v1/sites",
        headers={**bearer(token()), "X-Tenant-Id": str(seeded["tenant_b"])},
    )
    assert chosen.status_code == 200
    hosts = {item["normalized_host"] for item in chosen.json()["data"]}
    assert any(host.startswith(seeded["site_b"].hex[:8]) for host in hosts)

    refused = client.get(
        "/v1/sites", headers={**bearer(token()), "X-Tenant-Id": str(uuid4())}
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == "tenant_not_permitted"


@pytest.mark.asyncio
async def test_the_invited_role_is_the_role_the_route_enforces(
    client, engine, seeded
) -> None:
    """A viewer's token must not be able to do an owner's work.

    Authentication that resolves the right person with the wrong authority is
    not much better than none, and the role travels from the invitation through
    the membership into `TenantContext.require`.
    """
    await invite(engine, seeded["tenant_a"], seeded["inviter"], "ada@example.com", role="viewer")

    assert client.get("/v1/members", headers=bearer(token())).status_code == 200

    refused = client.post(
        "/v1/members/invitations",
        headers=bearer(token()),
        json={"email": "someone@example.com", "role": "editor"},
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == "forbidden"
