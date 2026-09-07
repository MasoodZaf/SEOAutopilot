"""The parts of the GA4 consumer that decide what data is written where.

Two consumers read the same stream. What keeps them apart is the connector type
in the claim and the scope set the credential must carry, so both are asserted
here rather than assumed -- reading GA4 with a Search Console grant would fail
at Google as `authorization_required` and send a tenant to a consent screen to
fix a bug on this side.
"""

from datetime import date

import pytest
from app.analytics.client import ANALYTICS_READONLY_SCOPE
from app.analytics.consumer import (
    CONNECTOR_TYPE,
    GROUP,
    REQUIRED_SCOPES,
    _normalized_url,
    parse_cursor,
)
from app.gsc.client import READONLY_SCOPE
from app.gsc.consumer import CONNECTOR_TYPE as GSC_CONNECTOR_TYPE
from app.gsc.consumer import GROUP as GSC_GROUP
from app.gsc.consumer import REQUIRED_SCOPES as GSC_REQUIRED_SCOPES


def test_the_two_syncs_claim_different_work_with_different_grants() -> None:
    assert CONNECTOR_TYPE != GSC_CONNECTOR_TYPE
    # Separate consumer groups, or one would consume the other's messages
    # before it could refuse them.
    assert GROUP != GSC_GROUP
    assert REQUIRED_SCOPES == frozenset({ANALYTICS_READONLY_SCOPE})
    assert GSC_REQUIRED_SCOPES == frozenset({READONLY_SCOPE})
    assert REQUIRED_SCOPES.isdisjoint(GSC_REQUIRED_SCOPES)


def test_the_cursor_is_read_back_in_this_connector_s_own_shape() -> None:
    cursor = parse_cursor({"day": "2026-09-01", "offset": 10_000})
    assert cursor is not None
    assert (cursor.day, cursor.offset) == (date(2026, 9, 1), 10_000)
    assert parse_cursor('{"day":"2026-09-02","offset":0}') is not None
    assert parse_cursor({}) is None
    assert parse_cursor(None) is None
    with pytest.raises(ValueError, match="invalid_sync_cursor"):
        parse_cursor({"day": "not-a-date", "offset": 0})


def test_a_reported_landing_page_joins_the_url_the_crawler_stored() -> None:
    """The join is by string, so this has to match the crawler exactly.

    The crawler stores origin plus path, no query, no trailing slash except at
    the root. A mismatch here does not error -- it silently resolves no page and
    every GA4 row loses its link to what was crawled.
    """
    origin = "https://thecalchive.com"
    assert _normalized_url(origin, "/emi-calculator") == "https://thecalchive.com/emi-calculator"
    assert _normalized_url(origin + "/", "/emi-calculator") == "https://thecalchive.com/emi-calculator"
    assert _normalized_url(origin, "/emi-calculator/") == "https://thecalchive.com/emi-calculator"
    assert _normalized_url(origin, "/emi-calculator?utm=x") == "https://thecalchive.com/emi-calculator"
    assert _normalized_url(origin, "/") == "https://thecalchive.com/"


def test_a_bucket_that_is_not_a_page_resolves_to_no_page() -> None:
    """`(other)` is real sessions with no page behind them.

    Returning an empty string makes the lookup find nothing, which is the
    honest answer; fabricating a URL would attach those sessions to a page that
    was never visited.
    """
    assert _normalized_url("https://thecalchive.com", "(other)") == ""
    assert _normalized_url(None, "/emi-calculator") == ""
    assert _normalized_url("", "/emi-calculator") == ""
