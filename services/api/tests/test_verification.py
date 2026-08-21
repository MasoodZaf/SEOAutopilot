import pytest

from app.services.verification import DnsTxtVerifier


class FakeResolver:
    def __init__(self, values: set[str]) -> None:
        self._values = values
        self.requested_name: str | None = None

    async def values(self, record_name: str) -> set[str]:
        self.requested_name = record_name
        return self._values


@pytest.mark.asyncio
async def test_dns_verifier_requires_exact_scoped_record() -> None:
    resolver = FakeResolver({"seo-autopilot-verification=expected"})
    verifier = DnsTxtVerifier(resolver)
    assert await verifier.verify("example.com", "expected") is True
    assert resolver.requested_name == "_seo-autopilot.example.com"
    assert await verifier.verify("example.com", "other") is False
