"""Exchanging a refresh token for a working access token.

A Google access token lives one hour. The OAuth callback stores one, along with
the refresh token that is supposed to renew it -- and until now nothing anywhere
read that refresh token. The sync worker simply refused once the access token
expired:

    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        raise ValueError("authorization_required")

which flips the connector to `reauthorization_required` and asks a human to
click through Google's consent screen again. Every hour. Search Console data
could therefore never have accumulated past the first hour after a consent, no
matter how the sync was scheduled.

Two failures have to stay distinguishable here, because they need opposite
responses. A refresh Google refuses with `invalid_grant` means the grant is
genuinely gone -- revoked, or expired through disuse -- and a person does have
to re-consent. Anything else is Google being unavailable, and treating that as a
revoked grant would send the tenant to a consent screen they never needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


class GoogleRefreshError(RuntimeError):
    """The refresh did not succeed, and might if it were tried again."""


class GoogleAuthorizationRevoked(RuntimeError):
    """The grant itself is gone. Only a person can put it back."""


@dataclass(frozen=True, slots=True)
class RefreshedToken:
    access_token: str
    expires_at: datetime
    scopes: frozenset[str]


class TokenRefresher:
    async def refresh(self, refresh_token: str) -> RefreshedToken:  # pragma: no cover
        raise NotImplementedError


class GoogleTokenHttpRefresher(TokenRefresher):
    def __init__(self, client: httpx.AsyncClient, *, client_id: str, client_secret: str) -> None:
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret

    async def refresh(self, refresh_token: str) -> RefreshedToken:
        try:
            response = await self._client.post(
                TOKEN_ENDPOINT,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        except httpx.TimeoutException as error:
            raise GoogleRefreshError("token_refresh_timeout") from error
        except httpx.HTTPError as error:
            raise GoogleRefreshError("token_refresh_unavailable") from error

        if response.status_code == 400:
            # The only status Google uses to say the grant is gone, and it says
            # which kind in the body. Anything else at 400 is a malformed
            # request on our side, which is not the tenant's problem to fix by
            # re-consenting.
            body = response.json() if response.headers.get(
                "content-type", ""
            ).startswith("application/json") else {}
            error_code = body.get("error") if isinstance(body, dict) else None
            if error_code in {"invalid_grant", "unauthorized_client"}:
                raise GoogleAuthorizationRevoked("authorization_required")
            raise GoogleRefreshError("token_refresh_rejected")
        if response.status_code >= 400:
            raise GoogleRefreshError(f"token_refresh_failed:{response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise GoogleRefreshError("token_refresh_response_invalid")
        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        # A refresh response usually echoes the scope; when it does not, the
        # grant is unchanged from the one already recorded, so the caller keeps
        # what it had rather than inventing a narrower set.
        scope = payload.get("scope")
        if (
            not isinstance(access_token, str)
            or not access_token
            or not isinstance(expires_in, (int, float))
            or expires_in <= 0
        ):
            raise GoogleRefreshError("token_refresh_response_invalid")
        return RefreshedToken(
            access_token=access_token,
            expires_at=datetime.now(UTC) + timedelta(seconds=float(expires_in)),
            scopes=frozenset(scope.split()) if isinstance(scope, str) else frozenset(),
        )
