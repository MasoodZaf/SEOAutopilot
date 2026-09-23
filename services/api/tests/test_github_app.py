"""The app credential: signing it, and what GitHub is allowed to answer with.

Everything in the installation flow rests on one assertion -- a JWT signed with
the app's private key -- and on believing GitHub's answer about which
repositories an installation covers. Both are worth pinning: a JWT with the
wrong claim shape is rejected at the far end with a 401 that says nothing, and a
misread repository list would let a connector report healthy while pointing at a
repository the tenant never granted.
"""

import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from app.services.github_app import (
    GitHubAppClient,
    GitHubAppError,
    GitHubUserClient,
    app_jwt,
    load_private_key,
)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


def _decode(segment: str) -> dict[str, object]:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def client(handler) -> GitHubAppClient:
    transport = httpx.MockTransport(handler)
    return GitHubAppClient(httpx.AsyncClient(transport=transport), "123456", PEM)


def test_the_assertion_verifies_against_the_apps_public_key() -> None:
    token = app_jwt("123456", PEM)
    header, claims, signature = token.split(".")

    signing_input = f"{header}.{claims}".encode()
    KEY.public_key().verify(
        base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)),
        signing_input,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

    assert _decode(header) == {"alg": "RS256", "typ": "JWT"}
    assert _decode(claims)["iss"] == "123456"


def test_the_assertion_is_backdated_and_expires_inside_githubs_limit() -> None:
    now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    claims = _decode(app_jwt("123456", PEM, now=now).split(".")[1])
    issued, expires = int(str(claims["iat"])), int(str(claims["exp"]))

    # Backdated, because a clock a few seconds fast makes GitHub reject a JWT
    # issued "in the future"; and well inside the ten minutes GitHub allows.
    assert issued < int(now.timestamp())
    assert 0 < expires - int(now.timestamp()) <= 600


def test_a_key_that_is_not_an_rsa_private_key_is_refused() -> None:
    curve = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    with pytest.raises(GitHubAppError, match="github_app_private_key_not_rsa"):
        load_private_key(curve)
    with pytest.raises(GitHubAppError, match="github_app_private_key_invalid"):
        load_private_key("not a pem")


@pytest.mark.asyncio
async def test_an_installation_token_is_read_with_the_repositories_it_covers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/app/installations/42/access_tokens"
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(
            201,
            json={
                "token": "ghs_installation",
                "expires_at": "2026-09-06T13:00:00Z",
                "repositories": [{"full_name": "MasoodZaf/mindTools"}],
            },
        )

    minted = await client(handler).mint_installation_token(42)

    assert minted.token == "ghs_installation"
    assert minted.repositories == ("MasoodZaf/mindTools",)
    assert minted.expires_at == datetime(2026, 9, 6, 13, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_token_for_every_repository_reports_no_narrowing() -> None:
    """An installation granted "all repositories" omits the list entirely.

    None and () mean different things here: no narrowing at all, versus a
    narrowing to nothing. Collapsing them would let a repository the tenant
    never granted pass the scope check at deploy time.
    """
    handler = lambda _: httpx.Response(
        201, json={"token": "t", "expires_at": "2026-09-06T13:00:00Z"}
    )
    assert (await client(handler).mint_installation_token(42)).repositories is None


@pytest.mark.asyncio
async def test_a_removed_installation_is_named_as_such() -> None:
    with pytest.raises(GitHubAppError, match="github_installation_not_found"):
        await client(lambda _: httpx.Response(404, json={})).mint_installation_token(42)


@pytest.mark.asyncio
async def test_a_malformed_token_response_is_refused_rather_than_used() -> None:
    handler = lambda _: httpx.Response(201, json={"token": "t"})
    with pytest.raises(GitHubAppError, match="github_installation_token_malformed"):
        await client(handler).mint_installation_token(42)


@pytest.mark.asyncio
async def test_the_repository_list_is_read_to_the_end() -> None:
    """A selection larger than one page must not be silently truncated.

    Stopping at the first page would refuse a repository the tenant did grant,
    which fails closed but tells them something untrue about their own install.
    """
    pages = {
        1: [{"full_name": f"acme/repo-{index}"} for index in range(100)],
        2: [{"full_name": "acme/last"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params)["page"])
        return httpx.Response(200, json={"repositories": pages[page]})

    names = await client(handler).installation_repositories("ghs_installation")

    assert len(names) == 101
    assert names[-1] == "acme/last"


@pytest.mark.asyncio
async def test_an_unreadable_installation_is_an_error_not_an_empty_grant() -> None:
    with pytest.raises(GitHubAppError, match="github_installation_repositories_failed:401"):
        await client(lambda _: httpx.Response(401, json={})).installation_repositories("t")


def user_client(handler) -> GitHubUserClient:
    return GitHubUserClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), "Iv1.id", "secret", "http://cb"
    )


@pytest.mark.asyncio
async def test_a_persons_repositories_are_read_to_the_end_and_filtered_to_push() -> None:
    def repo(number: int, **extra) -> dict:
        return {
            "id": number,
            "full_name": f"o/r{number}",
            "default_branch": "main",
            "permissions": {"push": True},
            **extra,
        }

    pages = {
        "1": [repo(n) for n in range(100)],
        "2": [
            repo(100, permissions={"admin": True, "push": False}),
            repo(101, permissions={"pull": True}),
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer ghu_person"
        assert request.url.path == "/user/installations/7/repositories"
        return httpx.Response(200, json={"repositories": pages[request.url.params["page"]]})

    found = await user_client(handler).pushable_repositories("ghu_person", 7, "o")

    # An admin can push; a reader cannot, and is not offered.
    assert len(found) == 101
    assert found[-1].id == 100
    assert {item.installation_id for item in found} == {7}


@pytest.mark.asyncio
async def test_a_refused_code_names_githubs_reason_and_nothing_else() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "github.com"
        return httpx.Response(200, json={"error": "bad_verification_code", "error_uri": "x"})

    with pytest.raises(GitHubAppError, match="^github_authorization_failed:bad_verification_code$"):
        await user_client(handler).exchange_code("stale")
