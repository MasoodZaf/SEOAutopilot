from dataclasses import dataclass
from typing import Any

import pytest
from app.competitors.fetch import (
    MAX_REDIRECT_HOPS,
    observe_competitor_page,
    parse_competitor_page,
    robots_allows,
)
from app.competitors.scan import normalize_competitor_url
from app.egress.guard import EgressBlocked, assert_public_https_target

HTML = """
<html><head>
  <title>  The best ROI calculator  </title>
  <meta name="description" content="Work out return on investment in seconds.">
  <script type="application/ld+json">{"@type":"FAQPage","name":"x"}</script>
  <script type="application/ld+json">{"@type":"Organization"}</script>
  <script>var secret = "should not be counted";</script>
  <style>.a{content:"nope"}</style>
</head><body>
  <h1>ROI calculator</h1><h2>How it works</h2><h2>Formula</h2>
  <p>One two three four five.</p>
  <a href="/pricing">Pricing</a>
  <a href="https://competitor.example/about">About</a>
  <a href="https://elsewhere.example/x">Offsite</a>
</body></html>
"""


@dataclass
class FakeResponse:
    status_code: int
    headers: dict[str, str]
    text: str


class FakeFetcher:
    def __init__(self, replies: dict[str, FakeResponse]) -> None:
        self.replies = replies
        self.requested: list[str] = []

    async def get(self, url: str, *, timeout: float, headers: dict[str, str]) -> Any:
        self.requested.append(url)
        if url not in self.replies:
            raise OSError("unexpected request")
        return self.replies[url]


def html_response(body: str = HTML) -> FakeResponse:
    return FakeResponse(200, {"content-type": "text/html; charset=utf-8"}, body)


# --- parsing ---


def test_parsing_keeps_structure_and_discards_the_body() -> None:
    evidence = parse_competitor_page(HTML, 200, "competitor.example")
    assert evidence.outcome == "observed"
    assert evidence.title == "The best ROI calculator"
    assert evidence.meta_description == "Work out return on investment in seconds."
    assert evidence.h1 == ["ROI calculator"]
    assert evidence.heading_count == 3
    assert evidence.structured_data_types == ["FAQPage", "Organization"]
    # Same-host and relative links count; an offsite link does not.
    assert evidence.internal_link_count == 2
    # Script and style contents are excluded from the word count.
    assert "should not be counted" not in str(evidence)
    assert evidence.content_hash is not None


def test_content_hash_changes_only_when_the_page_changes() -> None:
    baseline = parse_competitor_page(HTML, 200, "competitor.example")
    same = parse_competitor_page(HTML, 200, "competitor.example")
    changed = parse_competitor_page(HTML.replace("Formula", "Worked example"), 200, "competitor.example")
    assert baseline.content_hash == same.content_hash
    assert baseline.content_hash != changed.content_hash


# --- robots ---


def test_robots_disallow_blocks_and_a_longer_allow_carves_out() -> None:
    rules = "User-agent: *\nDisallow: /private\nAllow: /private/public-page\n"
    assert robots_allows(rules, "/pricing") is True
    assert robots_allows(rules, "/private/secret") is False
    assert robots_allows(rules, "/private/public-page") is True
    # Absent rules allow, matching the convention.
    assert robots_allows("", "/anything") is True


def test_robots_rules_for_another_agent_do_not_apply() -> None:
    rules = "User-agent: SomeOtherBot\nDisallow: /\n"
    assert robots_allows(rules, "/pricing") is True


# --- egress ---


def test_egress_guard_rejects_non_public_and_non_https_targets() -> None:
    for rejected in (
        "http://competitor.example/x",
        "https://user:pass@competitor.example/x",
        "https://127.0.0.1/x",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/x",
    ):
        with pytest.raises(EgressBlocked):
            assert_public_https_target(rejected)


def test_competitor_url_normalisation_is_strict() -> None:
    normalized, host = normalize_competitor_url("https://Competitor.Example/pricing/")
    assert normalized == "https://competitor.example/pricing"
    assert host == "competitor.example"
    for rejected in ("http://competitor.example/x", "https://user:pw@competitor.example/x", "https://localhost/x"):
        with pytest.raises(ValueError):
            normalize_competitor_url(rejected)


# --- fetching ---


@pytest.mark.asyncio
async def test_a_recorded_page_is_fetched_and_parsed() -> None:
    url = "https://example.com/roi"
    fetcher = FakeFetcher({url: html_response()})
    evidence = await observe_competitor_page(fetcher, url, "example.com", "")
    assert evidence.outcome == "observed"
    assert evidence.title == "The best ROI calculator"


@pytest.mark.asyncio
async def test_a_disallowed_path_is_recorded_rather_than_fetched() -> None:
    url = "https://example.com/private/x"
    fetcher = FakeFetcher({url: html_response()})
    evidence = await observe_competitor_page(
        fetcher, url, "example.com", "User-agent: *\nDisallow: /private\n"
    )
    assert evidence.outcome == "robots_disallowed"
    assert fetcher.requested == []


@pytest.mark.asyncio
async def test_a_redirect_off_the_competitor_host_is_not_followed() -> None:
    """Auto-following would let a redirect reach an unvetted destination."""
    start = "https://example.com/roi"
    fetcher = FakeFetcher(
        {
            start: FakeResponse(301, {"location": "https://169.254.169.254/latest"}, ""),
            "https://169.254.169.254/latest": html_response(),
        }
    )
    evidence = await observe_competitor_page(fetcher, start, "example.com", "")
    assert evidence.outcome == "unreachable"
    assert fetcher.requested == [start]


@pytest.mark.asyncio
async def test_a_same_host_redirect_is_followed_and_revalidated() -> None:
    start = "https://example.com/roi"
    target = "https://example.com/roi-calculator"
    fetcher = FakeFetcher(
        {start: FakeResponse(301, {"location": "/roi-calculator"}, ""), target: html_response()}
    )
    evidence = await observe_competitor_page(fetcher, start, "example.com", "")
    assert evidence.outcome == "observed"
    assert fetcher.requested == [start, target]


@pytest.mark.asyncio
async def test_a_redirect_loop_terminates() -> None:
    url = "https://example.com/loop"
    fetcher = FakeFetcher({url: FakeResponse(302, {"location": "/loop"}, "")})
    evidence = await observe_competitor_page(fetcher, url, "example.com", "")
    assert evidence.outcome == "unreachable"
    assert len(fetcher.requested) == MAX_REDIRECT_HOPS + 1


@pytest.mark.asyncio
async def test_a_non_html_response_is_recorded_without_parsing() -> None:
    url = "https://example.com/report.pdf"
    fetcher = FakeFetcher({url: FakeResponse(200, {"content-type": "application/pdf"}, "%PDF")})
    evidence = await observe_competitor_page(fetcher, url, "example.com", "")
    assert evidence.outcome == "not_html"
    assert evidence.http_status == 200
