import httpx
import pytest

from app.services.verification import DnsTxtVerifier, WebProofVerifier


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


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return ["10.0.0.1"] if host == "internal.example.com" else ["93.184.216.34"]

    monkeypatch.setattr("app.services.deployment_verification.resolve", resolve)


@pytest.mark.asyncio
async def test_file_proof_needs_the_exact_token_at_the_fixed_path() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="tok-1\n")

    verifier = WebProofVerifier("file", _client(handler))
    assert await verifier.verify("example.com", "tok-1") is True
    assert seen == ["https://example.com/.well-known/seo-autopilot-verification.txt"]
    assert await verifier.verify("example.com", "tok") is False


@pytest.mark.asyncio
async def test_a_page_that_merely_contains_the_token_is_not_a_file_proof() -> None:
    client = _client(lambda _: httpx.Response(200, text="<html>tok-1</html>"))
    assert await WebProofVerifier("file", client).verify("example.com", "tok-1") is False


@pytest.mark.asyncio
async def test_meta_proof_counts_only_in_the_head() -> None:
    tag = '<meta name="seo-autopilot-verification" content="tok-1">'
    head = _client(lambda _: httpx.Response(200, text=f"<html><head>{tag}</head><body></body>"))
    assert await WebProofVerifier("meta_tag", head).verify("example.com", "tok-1") is True
    # A visitor's comment renders in the body and must prove nothing.
    body = _client(lambda _: httpx.Response(200, text=f"<html><head></head><body>{tag}</body>"))
    assert await WebProofVerifier("meta_tag", body).verify("example.com", "tok-1") is False


@pytest.mark.asyncio
async def test_a_failed_response_is_no_proof() -> None:
    client = _client(lambda _: httpx.Response(404, text="tok-1"))
    assert await WebProofVerifier("file", client).verify("example.com", "tok-1") is False


@pytest.mark.asyncio
async def test_redirects_stay_on_the_site_and_on_https() -> None:
    def to(location: str):
        return _client(lambda request: httpx.Response(
            302 if request.url.host == "example.com" else 200,
            headers={"location": location},
            text="tok-1",
        ))

    www = to("https://www.example.com/.well-known/seo-autopilot-verification.txt")
    assert await WebProofVerifier("file", www).verify("example.com", "tok-1") is True
    other = to("https://attacker.test/proof.txt")
    assert await WebProofVerifier("file", other).verify("example.com", "tok-1") is False
    plain = to("http://www.example.com/proof.txt")
    assert await WebProofVerifier("file", plain).verify("example.com", "tok-1") is False


@pytest.mark.asyncio
async def test_a_host_on_a_private_address_is_refused_before_any_request() -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text="tok-1")

    verifier = WebProofVerifier("file", _client(handler))
    assert await verifier.verify("internal.example.com", "tok-1") is False
    assert called is False
