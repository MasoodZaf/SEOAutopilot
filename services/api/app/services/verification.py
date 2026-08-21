from typing import Protocol

import dns.asyncresolver
import dns.exception


class TxtResolver(Protocol):
    async def values(self, record_name: str) -> set[str]: ...


class DnsPythonTxtResolver:
    async def values(self, record_name: str) -> set[str]:
        answer = await dns.asyncresolver.resolve(record_name, "TXT", lifetime=5)
        return {b"".join(record.strings).decode("utf-8") for record in answer}


class DnsTxtVerifier:
    def __init__(self, resolver: TxtResolver | None = None) -> None:
        self.resolver = resolver or DnsPythonTxtResolver()

    async def verify(self, host: str, token: str) -> bool:
        expected = f"seo-autopilot-verification={token}"
        try:
            values = await self.resolver.values(f"_seo-autopilot.{host}")
        except dns.exception.DNSException:
            return False
        return expected in values


def get_site_verifier() -> DnsTxtVerifier:
    return DnsTxtVerifier()
