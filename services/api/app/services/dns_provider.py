from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class DnsProviderError(RuntimeError):
    """A safe DNS-provider error code that never contains provider response bodies or secrets."""


@dataclass(frozen=True)
class DnsZone:
    id: str
    name: str


@dataclass(frozen=True)
class DnsTxtRecord:
    id: str
    name: str
    content: str


class DnsProvider(Protocol):
    provider_key: str

    async def get_zone(self, zone_id: str, token: str) -> DnsZone: ...

    async def find_txt_record(
        self, zone_id: str, name: str, content: str, token: str
    ) -> DnsTxtRecord | None: ...

    async def create_txt_record(
        self, zone_id: str, name: str, content: str, token: str
    ) -> DnsTxtRecord: ...


SUPPORTED_DNS_PROVIDERS = {"cloudflare": "Cloudflare"}


def dns_provider_display_name(provider_key: str) -> str:
    return SUPPORTED_DNS_PROVIDERS.get(provider_key, provider_key)
