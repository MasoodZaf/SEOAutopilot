"""What the auth dependency does before any route sees a request.

The dependency has three jobs and they fail in three different ways, which is
the point of these: a missing provider is an operator's mistake, an unreachable
one is an outage on this side, and a bad token is the caller's problem. Reporting
any of them as another sends somebody to fix the wrong thing -- most damagingly,
reporting an outage as 401, which asks every user to sign in again to repair a
server they cannot reach.
"""

from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr

from app.core import auth
from app.core.config import Settings
from app.core.context import Role


@pytest.fixture(autouse=True)
def _clear_verifier():
    auth.reset_oidc_verifier()
    yield
    auth.reset_oidc_verifier()


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "development",
        "cursor_signing_key": "c" * 32,
    }
    values.update(overrides)
    return Settings(**values)  # pyright: ignore[reportCallIssue]


def credentials(token: str = "a-token") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.asyncio
async def test_no_provider_configured_is_reported_as_such() -> None:
    with pytest.raises(HTTPException) as raised:
        await auth.require_tenant_context(settings(), credentials())
    assert raised.value.status_code == 401
    assert raised.value.detail == "authentication_not_configured"


@pytest.mark.asyncio
async def test_an_unreachable_provider_is_an_outage_not_a_login_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = settings(
        oidc_issuer_url="https://issuer.example.com", oidc_audience="seo-autopilot"
    )

    async def unreachable(issuer: str, client: httpx.AsyncClient) -> str:
        raise httpx.ConnectError("no route to provider")

    monkeypatch.setattr(auth, "discover_jwks_uri", unreachable)
    with pytest.raises(HTTPException) as raised:
        await auth.require_tenant_context(configured, credentials())
    assert raised.value.status_code == 503
    assert raised.value.detail == "authentication_unavailable"


@pytest.mark.asyncio
async def test_a_rejected_token_reports_the_verifier_code_and_nothing_else() -> None:
    configured = settings(
        oidc_issuer_url="https://issuer.example.com",
        oidc_audience="seo-autopilot",
        oidc_jwks_uri="https://issuer.example.com/jwks",
    )
    with pytest.raises(HTTPException) as raised:
        await auth.require_tenant_context(configured, credentials("not-a-jwt"))
    assert raised.value.status_code == 401
    assert raised.value.detail == "token_malformed"


@pytest.mark.asyncio
async def test_a_request_with_no_credentials_is_unauthorized() -> None:
    configured = settings(
        oidc_issuer_url="https://issuer.example.com",
        oidc_audience="seo-autopilot",
        oidc_jwks_uri="https://issuer.example.com/jwks",
    )
    with pytest.raises(HTTPException) as raised:
        await auth.require_tenant_context(configured, None)
    assert raised.value.status_code == 401
    assert raised.value.detail == "unauthorized"


@pytest.mark.asyncio
async def test_the_development_pilot_token_still_works_and_skips_the_provider() -> None:
    """A local stack should not need an identity provider to run the UI.

    It is checked first and returns before anything network-bound happens, so a
    developer with no OIDC configuration is unaffected by all of the above.
    """
    context = await auth.require_tenant_context(
        settings(
            local_pilot_auth_enabled=True,
            local_pilot_auth_token=SecretStr("p" * 32),
        ),
        credentials("p" * 32),
    )
    assert context.role == Role.OWNER


def test_a_tenant_selector_is_parsed_or_refused() -> None:
    """Silently ignoring an unparseable selector would act on a different tenant."""
    assert auth._requested_tenant(None) is None
    assert auth._requested_tenant("  ") is None
    assert auth._requested_tenant("019d0000-0000-7000-8000-000000000011") == UUID(
        "019d0000-0000-7000-8000-000000000011"
    )
    with pytest.raises(HTTPException) as raised:
        auth._requested_tenant("not-a-uuid")
    assert raised.value.status_code == 400
    assert raised.value.detail == "tenant_selector_invalid"
