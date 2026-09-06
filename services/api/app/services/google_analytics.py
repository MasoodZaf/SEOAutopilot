"""Reading which GA4 properties an authorization actually covers.

A Search Console property names its own site -- `sc-domain:example.com` -- so
binding one to a verified site is a string comparison. A GA4 property does not:
`properties/123456789` says nothing about what it measures, and its display name
is whatever somebody typed. Accepting a property ref on that basis would let any
authorized account attach any property to any site, which is the same hole
`property_matches_site` exists to close on the Search Console side.

The hostname lives one level down, on the property's data streams: a web stream
carries the `defaultUri` of the site it collects from. So a property is bound to
a site only when one of its own streams points at the host the tenant already
proved they control.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

ANALYTICS_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

ACCOUNT_SUMMARIES_ENDPOINT = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
ADMIN_BASE = "https://analyticsadmin.googleapis.com/v1beta"

# An account summary page holds up to 200 properties. These bound a listing so a
# very large account cannot turn one connect click into an unbounded walk.
MAX_SUMMARY_PAGES = 10
MAX_PROPERTIES_INSPECTED = 100


class GoogleAnalyticsError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AnalyticsProperty:
    """One GA4 property and the hosts its web streams collect from."""

    property_ref: str
    display_name: str
    stream_hosts: tuple[str, ...]


class AnalyticsAdminProvider(Protocol):
    async def list_properties(self, access_token: str) -> list[AnalyticsProperty]: ...


def property_matches_site(property_: AnalyticsProperty, normalized_host: str) -> bool:
    """Does one of this property's own streams collect from the verified host?

    A stream on `example.com` covers `www.example.com` too, because that is how
    a site is usually instrumented. The reverse is not true: proving control of
    `blog.example.com` must not attach a property measuring the whole domain.
    """
    expected = normalized_host.rstrip(".").lower()
    if not expected:
        return False
    for host in property_.stream_hosts:
        candidate = host.rstrip(".").lower()
        if not candidate:
            continue
        if expected == candidate or expected.endswith(f".{candidate}"):
            return True
    return False


def stream_host(default_uri: str) -> str | None:
    """The host a web stream collects from, or nothing if it is unusable."""
    parsed = urlsplit(default_uri if "://" in default_uri else f"https://{default_uri}")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed.hostname.lower()


class AnalyticsAdminHttpClient:
    """Reads account summaries, then each property's streams for its hosts."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def list_properties(self, access_token: str) -> list[AnalyticsProperty]:
        headers = {"Authorization": f"Bearer {access_token}"}
        summaries = await self._account_summaries(headers)
        properties: list[AnalyticsProperty] = []
        for property_ref, display_name in summaries[:MAX_PROPERTIES_INSPECTED]:
            hosts = await self._stream_hosts(headers, property_ref)
            properties.append(AnalyticsProperty(property_ref, display_name, hosts))
        return properties

    async def _account_summaries(self, headers: dict[str, str]) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        page_token: str | None = None
        for _ in range(MAX_SUMMARY_PAGES):
            params = {"pageSize": "200"}
            if page_token:
                params["pageToken"] = page_token
            response = await self.client.get(
                ACCOUNT_SUMMARIES_ENDPOINT, headers=headers, params=params
            )
            if response.status_code >= 400:
                raise GoogleAnalyticsError("analytics_property_list_failed")
            payload = _object(response.json())
            for account in _list(payload.get("accountSummaries", [])):
                for summary in _list(_object(account).get("propertySummaries", [])):
                    entry = _object(summary)
                    property_ref = entry.get("property")
                    if not isinstance(property_ref, str) or not property_ref:
                        raise GoogleAnalyticsError("analytics_property_response_invalid")
                    name = entry.get("displayName")
                    found.append((property_ref, name if isinstance(name, str) else property_ref))
            next_token = payload.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return found
            page_token = next_token
        return found

    async def _stream_hosts(self, headers: dict[str, str], property_ref: str) -> tuple[str, ...]:
        response = await self.client.get(
            f"{ADMIN_BASE}/{property_ref}/dataStreams",
            headers=headers,
            params={"pageSize": "200"},
        )
        if response.status_code >= 400:
            # A property whose streams cannot be read cannot be bound to a site.
            # Returning no hosts refuses it; raising would fail the whole listing
            # because one property in the account is unreadable.
            return ()
        hosts: list[str] = []
        for stream in _list(_object(response.json()).get("dataStreams", [])):
            web = _object(stream).get("webStreamData")
            if not isinstance(web, dict):
                continue
            default_uri = web.get("defaultUri")
            if not isinstance(default_uri, str):
                continue
            host = stream_host(default_uri)
            if host:
                hosts.append(host)
        return tuple(dict.fromkeys(hosts))


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GoogleAnalyticsError("analytics_response_invalid")
    return value


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise GoogleAnalyticsError("analytics_response_invalid")
    return value
