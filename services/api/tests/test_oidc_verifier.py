"""What a token has to survive before it is allowed to mean anything.

This is the one place in the service that parses input an attacker fully
controls and decides an identity from it, so the checks a JWT library leaves
optional are the ones worth pinning: the algorithm cannot come from the token,
another application's token from the same provider is not ours, an expired token
is not "nearly valid", and an address the provider did not verify is a claim
rather than a fact.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.oidc import (
    OidcConfigurationError,
    OidcVerificationError,
    OidcVerifier,
    discover_jwks_uri,
)

ISSUER = "https://issuer.example.com"
AUDIENCE = "seo-autopilot"
KID = "test-key-1"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwks_body(key: rsa.RSAPrivateKey = _key) -> dict[str, Any]:
    numbers = key.public_key().public_numbers()

    def b64(value: int) -> str:
        return _b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))

    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": KID,
                "use": "sig",
                "alg": "RS256",
                "n": b64(numbers.n),
                "e": b64(numbers.e),
            }
        ]
    }


def token(
    *,
    key: rsa.RSAPrivateKey = _key,
    algorithm: str = "RS256",
    kid: str = KID,
    **claims: Any,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "provider-subject-1",
        "email": "ada@example.com",
        "email_verified": True,
        "name": "Ada",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
    }
    payload.update(claims)
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": kid})  # pyright: ignore[reportArgumentType]


def verifier(handler: Any = None) -> OidcVerifier:
    """A verifier whose JWKS client is served from an in-process transport."""
    served = handler or (lambda request: httpx.Response(200, json=jwks_body()))
    built = OidcVerifier(
        issuer=ISSUER, audience=AUDIENCE, jwks_uri=f"{ISSUER}/jwks", cache_seconds=600
    )
    # PyJWKClient fetches over urllib; give it the body directly instead of a
    # network. The transport shape stays for the discovery tests below.
    built._jwks.fetch_data = lambda: _record(served)  # pyright: ignore[reportAttributeAccessIssue]
    return built


def _record(handler: Any) -> dict[str, Any]:
    response = handler(httpx.Request("GET", f"{ISSUER}/jwks"))
    if response.status_code >= 400:
        raise httpx.HTTPError("jwks_unavailable")
    return json.loads(response.content)


def test_a_valid_token_yields_the_identity_and_nothing_more() -> None:
    identity = verifier().verify(token())

    assert identity.issuer == ISSUER
    assert identity.subject == "provider-subject-1"
    assert identity.email_normalized == "ada@example.com"
    assert identity.email_verified is True
    assert identity.display_name == "Ada"


def test_an_uppercase_address_normalises_so_an_invitation_still_matches() -> None:
    identity = verifier().verify(token(email="Ada@Example.COM"))
    assert identity.email_normalized == "ada@example.com"


def test_a_token_signed_by_another_key_is_refused() -> None:
    with pytest.raises(OidcVerificationError):
        verifier().verify(token(key=_other_key))


def test_the_algorithm_never_comes_from_the_token() -> None:
    """`none` and HMAC-with-the-public-key are the two classic forgeries.

    Both work only if the verifier lets the token's own header choose how it is
    checked, so both are refused before a key is selected. The HMAC one is
    assembled by hand because PyJWT will not even mint it.
    """
    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x"}, key="", algorithm="none"
    )
    with pytest.raises(OidcVerificationError):
        verifier().verify(unsigned)

    public_pem = _key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    now = datetime.now(UTC)
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    body = _b64(
        json.dumps(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "x",
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
            }
        ).encode()
    )
    signing_input = f"{header}.{body}".encode()
    signature = _b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
    with pytest.raises(OidcVerificationError):
        verifier().verify(f"{header}.{body}.{signature}")


def test_another_applications_token_from_the_same_provider_is_not_ours() -> None:
    with pytest.raises(OidcVerificationError, match="token_audience_mismatch"):
        verifier().verify(token(aud="some-other-application"))


def test_a_token_from_another_issuer_is_refused() -> None:
    with pytest.raises(OidcVerificationError):
        verifier().verify(token(iss="https://attacker.example.com"))


def test_an_expired_token_is_refused_rather_than_tolerated() -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    with pytest.raises(OidcVerificationError, match="token_expired"):
        verifier().verify(
            token(iat=int(past.timestamp()), exp=int((past + timedelta(minutes=5)).timestamp()))
        )


def test_a_token_without_a_subject_or_an_address_establishes_nobody() -> None:
    with pytest.raises(OidcVerificationError, match="token_subject_missing"):
        verifier().verify(token(sub=""))
    with pytest.raises(OidcVerificationError, match="token_email_missing"):
        verifier().verify(token(email="not-an-address"))


def test_an_unverified_address_is_reported_rather_than_assumed() -> None:
    """The verifier records the provider's answer; the resolver decides on it.

    Refusing here would be wrong: an existing user recognised by subject does
    not need their address re-verified, and only a first login matches on it.
    """
    identity = verifier().verify(token(email_verified=False))
    assert identity.email_verified is False


def test_a_malformed_token_never_reaches_the_key_lookup() -> None:
    with pytest.raises(OidcVerificationError, match="token_malformed"):
        verifier().verify("not-a-jwt")


@pytest.mark.asyncio
async def test_discovery_refuses_a_document_naming_a_different_issuer() -> None:
    """A discovery document is fetched from the issuer and claims one.

    If those disagree, the safest reading is that this is not the provider it
    was fetched from, and its key set should not be trusted to verify tokens
    said to come from the configured one.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"issuer": "https://elsewhere.example.com", "jwks_uri": f"{ISSUER}/jwks"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OidcConfigurationError, match="issuer_mismatch"):
            await discover_jwks_uri(ISSUER, client)


@pytest.mark.asyncio
async def test_discovery_refuses_a_plaintext_key_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": "http://issuer/jwks"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OidcConfigurationError, match="oidc_discovery_invalid"):
            await discover_jwks_uri(ISSUER, client)


@pytest.mark.asyncio
async def test_discovery_returns_the_key_set_location() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/.well-known/openid-configuration"
        return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/jwks"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await discover_jwks_uri(ISSUER, client) == f"{ISSUER}/jwks"
