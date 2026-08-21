from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SITES_ENDPOINT = "https://www.googleapis.com/webmasters/v3/sites"


class GoogleOAuthError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GoogleTokenGrant:
    access_token: str
    refresh_token: str
    scopes: frozenset[str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class GoogleProperty:
    property_ref: str
    permission_level: str


class GoogleOAuthProvider(Protocol):
    async def exchange_code(self, code: str) -> GoogleTokenGrant: ...

    async def list_properties(self, access_token: str) -> list[GoogleProperty]: ...


class GoogleOAuthHttpClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        self.client = client
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    async def exchange_code(self, code: str) -> GoogleTokenGrant:
        response = await self.client.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if response.status_code >= 400:
            raise GoogleOAuthError("oauth_code_exchange_failed")
        payload = self._object(response.json())
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        scope = payload.get("scope")
        expires_in = payload.get("expires_in")
        if (
            not isinstance(access_token, str)
            or not isinstance(refresh_token, str)
            or not isinstance(scope, str)
            or not isinstance(expires_in, (int, float))
            or expires_in <= 0
        ):
            raise GoogleOAuthError("oauth_token_response_invalid")
        return GoogleTokenGrant(
            access_token=access_token,
            refresh_token=refresh_token,
            scopes=frozenset(scope.split()),
            expires_at=datetime.now(UTC) + timedelta(seconds=float(expires_in)),
        )

    async def list_properties(self, access_token: str) -> list[GoogleProperty]:
        response = await self.client.get(
            SITES_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
        )
        if response.status_code >= 400:
            raise GoogleOAuthError("google_property_list_failed")
        payload = self._object(response.json())
        entries = payload.get("siteEntry", [])
        if not isinstance(entries, list):
            raise GoogleOAuthError("google_property_response_invalid")
        result: list[GoogleProperty] = []
        for value in entries:
            if not isinstance(value, dict):
                raise GoogleOAuthError("google_property_response_invalid")
            property_ref = value.get("siteUrl")
            permission_level = value.get("permissionLevel")
            if not isinstance(property_ref, str) or not isinstance(permission_level, str):
                raise GoogleOAuthError("google_property_response_invalid")
            result.append(GoogleProperty(property_ref, permission_level))
        return result

    @staticmethod
    def _object(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise GoogleOAuthError("google_response_invalid")
        return value
