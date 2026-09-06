"""Minting short-lived credentials for a GitHub App installation.

A personal access token is the wrong shape for a multi-tenant product. Somebody
has to create it, somebody has to paste it, it is scoped to a human rather than
to the work, it does not expire, and revoking it means finding the person who
made it. Every one of those is a problem this service has already hit once.

A GitHub App installation inverts all of that. The tenant installs the app on
the repositories they choose; GitHub records the grant. This service holds one
private key -- the app's, not a tenant's -- signs a short JWT with it, and
exchanges that for an installation token that lives one hour and is scoped to
exactly the repositories the tenant selected. Nobody types a credential, nothing
long-lived is stored, and the tenant revokes access by uninstalling the app.

The JWT is assembled here rather than pulled in as a dependency: it is two
base64url segments and one RSA signature, and `cryptography` is already a
dependency because the connector secret store needs it.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

GITHUB_API = "https://api.github.com"
API_VERSION = "2022-11-28"

# GitHub rejects a JWT more than ten minutes in the future, and clocks drift, so
# the window is opened slightly in the past and closed well inside the limit.
JWT_BACKDATE = timedelta(seconds=60)
JWT_LIFETIME = timedelta(minutes=8)

# Paging past this many repositories is not reading a selection, it is looping
# on a malformed response.
MAX_REPOSITORY_PAGES = 20


class GitHubAppError(Exception):
    """The app credential could not be used. The caller has to see why."""


@dataclass(frozen=True, slots=True)
class InstallationToken:
    token: str
    expires_at: datetime
    repositories: tuple[str, ...] | None
    """The repositories the token can reach, when GitHub scoped it to a subset."""


def _segment(payload: dict[str, object]) -> bytes:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def load_private_key(pem: str) -> rsa.RSAPrivateKey:
    """Parse the app's PEM, refusing anything that is not an RSA private key."""
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError) as error:
        raise GitHubAppError("github_app_private_key_invalid") from error
    if not isinstance(key, rsa.RSAPrivateKey):
        raise GitHubAppError("github_app_private_key_not_rsa")
    return key


def app_jwt(app_id: str, private_key_pem: str, *, now: datetime | None = None) -> str:
    """A short-lived assertion that this process holds the app's private key."""
    key = load_private_key(private_key_pem)
    issued = now or datetime.now(UTC)
    claims = {
        "iat": int((issued - JWT_BACKDATE).timestamp()),
        "exp": int((issued + JWT_LIFETIME).timestamp()),
        "iss": app_id,
    }
    signing_input = _segment({"alg": "RS256", "typ": "JWT"}) + b"." + _segment(claims)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return (signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode(
        "ascii"
    )


def _repository_names(listed: object) -> tuple[str, ...]:
    if not isinstance(listed, list):
        raise GitHubAppError("github_installation_repositories_malformed")
    return tuple(
        str(item["full_name"])
        for item in listed
        if isinstance(item, dict) and "full_name" in item
    )


class GitHubAppClient:
    """Reads an installation and mints tokens for it.

    Redirects are the caller's to disallow: a redirect off api.github.com would
    carry the app JWT -- the one credential that can act as every installation --
    to wherever it pointed.
    """

    def __init__(self, client: httpx.AsyncClient, app_id: str, private_key_pem: str) -> None:
        self._client = client
        self._app_id = app_id
        self._private_key_pem = private_key_pem

    def _app_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {app_jwt(self._app_id, self._private_key_pem)}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }

    async def _request(
        self, method: str, path: str, *, headers: dict[str, str]
    ) -> httpx.Response:
        try:
            return await self._client.request(method, f"{GITHUB_API}{path}", headers=headers)
        except httpx.TimeoutException as error:
            raise GitHubAppError("github_timeout") from error
        except httpx.HTTPError as error:
            raise GitHubAppError("github_unavailable") from error

    async def installation(self, installation_id: int) -> dict[str, object]:
        """The installation as GitHub sees it: who installed it, and on what."""
        response = await self._request(
            "GET", f"/app/installations/{installation_id}", headers=self._app_headers()
        )
        if response.status_code == 404:
            raise GitHubAppError("github_installation_not_found")
        if response.status_code != 200:
            raise GitHubAppError(f"github_installation_lookup_failed:{response.status_code}")
        body = response.json()
        if not isinstance(body, dict):
            raise GitHubAppError("github_installation_malformed")
        return body

    async def mint_installation_token(self, installation_id: int) -> InstallationToken:
        """A token good for about an hour, which is never written down."""
        response = await self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            headers=self._app_headers(),
        )
        if response.status_code == 404:
            raise GitHubAppError("github_installation_not_found")
        if response.status_code != 201:
            raise GitHubAppError(f"github_installation_token_failed:{response.status_code}")
        body = response.json()
        token = body.get("token") if isinstance(body, dict) else None
        expires = body.get("expires_at") if isinstance(body, dict) else None
        if not isinstance(token, str) or not isinstance(expires, str):
            raise GitHubAppError("github_installation_token_malformed")
        listed = body.get("repositories")
        return InstallationToken(
            token=token,
            expires_at=datetime.fromisoformat(expires),
            repositories=_repository_names(listed) if isinstance(listed, list) else None,
        )

    async def installation_repositories(self, token: str) -> tuple[str, ...]:
        """Every repository this installation token can reach.

        Used to prove the tenant actually granted the repository they asked to
        deploy to, rather than trusting the name they typed into a form.
        """
        names: list[str] = []
        for page in range(1, MAX_REPOSITORY_PAGES + 1):
            response = await self._request(
                "GET",
                f"/installation/repositories?per_page=100&page={page}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": API_VERSION,
                },
            )
            if response.status_code != 200:
                raise GitHubAppError(
                    f"github_installation_repositories_failed:{response.status_code}"
                )
            body = response.json()
            listed = body.get("repositories") if isinstance(body, dict) else None
            if not isinstance(listed, list):
                raise GitHubAppError("github_installation_repositories_malformed")
            names.extend(_repository_names(listed))
            if len(listed) < 100:
                return tuple(names)
        raise GitHubAppError("github_installation_repositories_too_many")
