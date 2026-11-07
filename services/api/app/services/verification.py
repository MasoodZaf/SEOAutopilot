from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import dns.asyncresolver
import dns.exception
import httpx

from app.services.deployment_verification import UnsafeDestination, require_public

PROOF_FILE_PATH = "/.well-known/seo-autopilot-verification.txt"
PROOF_META_NAME = "seo-autopilot-verification"
MAX_PROOF_BYTES = 512 * 1024
MAX_PROOF_REDIRECTS = 3


class SiteVerifier(Protocol):
    async def verify(self, host: str, token: str) -> bool: ...


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


class _HeadMetaParser(HTMLParser):
    """Collect one named meta tag from <head>, and stop at the body.

    Only the head counts. Comments and profiles render in the body, so a visitor
    who can post text on a page must not be able to prove ownership of it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: set[str] = set()
        self.done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            self.done = True
        if self.done or tag != "meta":
            return
        fields = dict(attrs)
        if fields.get("name") == PROOF_META_NAME and fields.get("content"):
            self.values.add(str(fields["content"]).strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self.done = True


def meta_proof_present(html: str, token: str) -> bool:
    parser = _HeadMetaParser()
    parser.feed(html)
    return token in parser.values


def _same_site(host: str, url: str) -> bool:
    """A redirect may move between apex and www, over https, and nowhere else."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    return parts.hostname.lower().removeprefix("www.") == host.lower().removeprefix("www.")


async def _read_proof(client: httpx.AsyncClient, host: str, url: str) -> str | None:
    """The body at `url` if it answers 200 on this site, else None.

    The reply is only compared with the token. It is never stored or shown, so a
    hostile site can learn nothing through this fetch beyond yes or no.
    """
    for _ in range(MAX_PROOF_REDIRECTS + 1):
        if not _same_site(host, url):
            return None
        await require_public(url)
        async with client.stream("GET", url, follow_redirects=False) as response:
            location = response.headers.get("location")
            if response.is_redirect and location:
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                return None
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_PROOF_BYTES:
                    break
            return bytes(body[:MAX_PROOF_BYTES]).decode("utf-8", "replace")
    return None


class WebProofVerifier:
    """Proof of control from the site itself: a file, or a meta tag on the home page."""

    def __init__(self, method: str, client: httpx.AsyncClient | None = None) -> None:
        self.method = method
        self.client = client

    async def verify(self, host: str, token: str) -> bool:
        path = PROOF_FILE_PATH if self.method == "file" else "/"
        try:
            if self.client is not None:
                body = await _read_proof(self.client, host, f"https://{host}{path}")
            else:
                async with httpx.AsyncClient(
                    timeout=10.0, headers={"User-Agent": "SEOAutopilot-Verifier/1.0"}
                ) as client:
                    body = await _read_proof(client, host, f"https://{host}{path}")
        except (httpx.HTTPError, UnsafeDestination):
            return False
        if body is None:
            return False
        if self.method == "file":
            return body.strip() == token
        return meta_proof_present(body, token)


def get_site_verifier() -> DnsTxtVerifier:
    return DnsTxtVerifier()


def verifier_for(method: str, dns_verifier: SiteVerifier) -> SiteVerifier:
    return dns_verifier if method == "dns_txt" else WebProofVerifier(method)
