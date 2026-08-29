from __future__ import annotations

import httpx

from app.services.dns_provider import DnsProviderError, DnsTxtRecord, DnsZone


class CloudflareDnsHttpClient:
    base_url = "https://api.cloudflare.com/client/v4"
    provider_key = "cloudflare"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _result(response: httpx.Response) -> object:
        if response.status_code >= 400:
            raise DnsProviderError("dns_provider_request_failed")
        try:
            body = response.json()
        except ValueError as error:
            raise DnsProviderError("dns_provider_response_invalid") from error
        if not isinstance(body, dict) or body.get("success") is not True:
            raise DnsProviderError("dns_provider_request_failed")
        return body.get("result")

    async def get_zone(self, zone_id: str, token: str) -> DnsZone:
        try:
            response = await self.client.get(
                f"{self.base_url}/zones/{zone_id}", headers=self._headers(token)
            )
        except httpx.HTTPError as error:
            raise DnsProviderError("dns_provider_unavailable") from error
        result = self._result(response)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("id"), str)
            or not isinstance(result.get("name"), str)
        ):
            raise DnsProviderError("dns_provider_response_invalid")
        return DnsZone(id=result["id"], name=result["name"].rstrip(".").lower())

    async def find_txt_record(
        self, zone_id: str, name: str, content: str, token: str
    ) -> DnsTxtRecord | None:
        try:
            response = await self.client.get(
                f"{self.base_url}/zones/{zone_id}/dns_records",
                params={"type": "TXT", "name": name},
                headers=self._headers(token),
            )
        except httpx.HTTPError as error:
            raise DnsProviderError("dns_provider_unavailable") from error
        result = self._result(response)
        if not isinstance(result, list):
            raise DnsProviderError("dns_provider_response_invalid")
        for item in result:
            if (
                isinstance(item, dict)
                and item.get("type") == "TXT"
                and item.get("name") == name
                and item.get("content") == content
                and isinstance(item.get("id"), str)
            ):
                return DnsTxtRecord(id=item["id"], name=name, content=content)
        return None

    async def create_txt_record(
        self, zone_id: str, name: str, content: str, token: str
    ) -> DnsTxtRecord:
        try:
            response = await self.client.post(
                f"{self.base_url}/zones/{zone_id}/dns_records",
                headers={**self._headers(token), "Content-Type": "application/json"},
                json={
                    "type": "TXT",
                    "name": name,
                    "content": content,
                    "ttl": 300,
                    "comment": "SEO Autopilot ownership verification",
                },
            )
        except httpx.HTTPError as error:
            raise DnsProviderError("dns_provider_unavailable") from error
        result = self._result(response)
        if not isinstance(result, dict) or not isinstance(result.get("id"), str):
            raise DnsProviderError("dns_provider_response_invalid")
        return DnsTxtRecord(id=result["id"], name=name, content=content)
