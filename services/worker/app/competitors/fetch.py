"""Fetches the exact competitor URLs a human put on record.

There is no discovery here. The scan requests only stored URLs, one per page
row, after checking that the host still matches its competitor record and that
the site's robots.txt permits the path. Only structural evidence is kept: never
the body text, only a hash proving whether it changed.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from app.egress.guard import EgressBlocked, assert_public_https_target

logger = logging.getLogger(__name__)

USER_AGENT = "SEOAutopilotBot"
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_HEADINGS = 50
MAX_TITLE_LENGTH = 1000
MAX_DESCRIPTION_LENGTH = 2000
MAX_STRUCTURED_TYPES = 20
# Politeness: never request more than this from one competitor per scan.
MAX_PAGES_PER_COMPETITOR = 25
# Redirects are followed manually so every hop is revalidated. Auto-following
# would let a redirect reach private space after the guard already passed.
MAX_REDIRECT_HOPS = 3


@dataclass(frozen=True, slots=True)
class FetchedPage:
    status: int
    content_type: str
    body: str
    location: str | None = None


class Fetcher(Protocol):
    async def get(self, url: str, *, timeout: float, headers: dict[str, str]) -> Any: ...


@dataclass(frozen=True, slots=True)
class CompetitorEvidence:
    outcome: str
    http_status: int | None = None
    title: str | None = None
    meta_description: str | None = None
    h1: list[str] = field(default_factory=list)
    heading_count: int = 0
    word_count: int = 0
    internal_link_count: int = 0
    structured_data_types: list[str] = field(default_factory=list)
    content_hash: str | None = None


TAG_PATTERN = re.compile(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>")
TITLE_PATTERN = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
DESCRIPTION_PATTERN = re.compile(
    r'(?is)<meta[^>]+name=["\']description["\'][^>]*content=["\'](.*?)["\']'
)
H1_PATTERN = re.compile(r"(?is)<h1[^>]*>(.*?)</h1>")
HEADING_PATTERN = re.compile(r"(?is)<h[1-6][^>]*>")
LDJSON_PATTERN = re.compile(
    r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
)
TYPE_PATTERN = re.compile(r'(?is)"@type"\s*:\s*"([A-Za-z][A-Za-z0-9]{0,60})"')
ANCHOR_PATTERN = re.compile(r'(?is)<a[^>]+href=["\'](.*?)["\']')
MARKUP_PATTERN = re.compile(r"(?s)<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


def _text(raw: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", MARKUP_PATTERN.sub(" ", raw)).strip()


def parse_competitor_page(body: str, status: int, host: str) -> CompetitorEvidence:
    """Extract structure only. The body itself is never returned or stored."""
    # JSON-LD lives inside a <script> block, so it must be read from the raw
    # body before script contents are stripped for text extraction.
    types: list[str] = []
    for block in LDJSON_PATTERN.findall(body):
        types.extend(TYPE_PATTERN.findall(block))
        if len(types) >= MAX_STRUCTURED_TYPES:
            break

    stripped = TAG_PATTERN.sub(" ", body)

    title_match = TITLE_PATTERN.search(stripped)
    description_match = DESCRIPTION_PATTERN.search(stripped)
    headings = [_text(match) for match in H1_PATTERN.findall(stripped)][:MAX_HEADINGS]

    internal_links = 0
    for href in ANCHOR_PATTERN.findall(stripped):
        candidate = href.strip()
        if candidate.startswith("/") and not candidate.startswith("//"):
            internal_links += 1
            continue
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            continue
        if parsed.hostname and parsed.hostname.rstrip(".").lower() == host:
            internal_links += 1

    visible = _text(stripped)
    return CompetitorEvidence(
        outcome="observed",
        http_status=status,
        title=(_text(title_match.group(1))[:MAX_TITLE_LENGTH] if title_match else None),
        meta_description=(
            _text(description_match.group(1))[:MAX_DESCRIPTION_LENGTH]
            if description_match
            else None
        ),
        h1=[item for item in headings if item][:10],
        heading_count=len(HEADING_PATTERN.findall(stripped)),
        word_count=len(visible.split()),
        internal_link_count=internal_links,
        structured_data_types=sorted(set(types))[:MAX_STRUCTURED_TYPES],
        # Records that the page changed without retaining what it said.
        content_hash=hashlib.sha256(visible.encode("utf-8")).hexdigest(),
    )


def robots_allows(robots_text: str, path: str) -> bool:
    """Conservative robots check for our own user agent and the wildcard group.

    Unparseable or absent robots text allows the request, matching the
    convention; an explicit Disallow prefix blocks it.
    """
    applicable = False
    disallowed: list[str] = []
    allowed: list[str] = []
    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field_name, _, value = line.partition(":")
        field_name = field_name.strip().lower()
        value = value.strip()
        if field_name == "user-agent":
            applicable = value == "*" or value.lower() == USER_AGENT.lower()
        elif applicable and field_name == "disallow" and value:
            disallowed.append(value)
        elif applicable and field_name == "allow" and value:
            allowed.append(value)
    # Longest match wins, so a specific Allow can carve out a broad Disallow.
    best_disallow = max((rule for rule in disallowed if path.startswith(rule)), key=len, default="")
    best_allow = max((rule for rule in allowed if path.startswith(rule)), key=len, default="")
    if not best_disallow:
        return True
    return len(best_allow) >= len(best_disallow)


async def fetch_text(fetcher: Fetcher, url: str) -> FetchedPage:
    response = await fetcher.get(
        url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    body = getattr(response, "text", "") or ""
    if len(body.encode("utf-8", "ignore")) > MAX_RESPONSE_BYTES:
        raise EgressBlocked("response_too_large")
    headers = getattr(response, "headers", {})
    return FetchedPage(
        status=int(getattr(response, "status_code", 0)),
        content_type=str(headers.get("content-type", "")),
        body=body,
        location=headers.get("location"),
    )


async def observe_competitor_page(
    fetcher: Fetcher, url: str, expected_host: str, robots_text: str
) -> CompetitorEvidence:
    """Fetch one recorded URL, following redirects only within the same host.

    Each hop is revalidated against the egress guard, the competitor's host, and
    robots. A redirect that leaves the competitor's host ends the attempt rather
    than being followed somewhere unvetted.
    """
    current = url
    for _ in range(MAX_REDIRECT_HOPS + 1):
        try:
            host = assert_public_https_target(current)
        except EgressBlocked:
            return CompetitorEvidence(outcome="unreachable")
        if host != expected_host:
            # The URL no longer matches the competitor record it was filed under.
            return CompetitorEvidence(outcome="unreachable")
        if not robots_allows(robots_text, urlsplit(current).path or "/"):
            return CompetitorEvidence(outcome="robots_disallowed")

        try:
            page = await fetch_text(fetcher, current)
        except EgressBlocked as error:
            return CompetitorEvidence(
                outcome="too_large" if error.code == "response_too_large" else "unreachable"
            )
        except (httpx.HTTPError, OSError, ValueError, TimeoutError):
            # Transport failures are recorded as unreachable; the destination
            # and any response body stay out of the log.
            logger.warning("competitor fetch failed", extra={"competitor_host": expected_host})
            return CompetitorEvidence(outcome="unreachable")

        if 300 <= page.status < 400 and page.location:
            current = urljoin(current, page.location)
            continue
        if "text/html" not in page.content_type.lower():
            return CompetitorEvidence(outcome="not_html", http_status=page.status)
        return parse_competitor_page(page.body, page.status, expected_host)
    return CompetitorEvidence(outcome="unreachable")
