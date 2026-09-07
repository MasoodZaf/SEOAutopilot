"""Binding a GA4 property to a site nobody has to take on trust.

`properties/123456789` names nothing. Its display name is whatever somebody
typed. So the only fact that binds a property to a site is its data streams,
and these pin the cases where a plausible implementation would attach the wrong
property: a subdomain reaching up to claim the parent domain, a property whose
streams cannot be read, a lookalike host.
"""

import httpx
import pytest

from app.services.google_analytics import (
    AnalyticsAdminHttpClient,
    AnalyticsProperty,
    GoogleAnalyticsError,
    property_matches_site,
    stream_host,
)


def summaries(*properties: tuple[str, str], next_token: str | None = None) -> dict:
    payload: dict[str, object] = {
        "accountSummaries": [
            {
                "account": "accounts/1",
                "propertySummaries": [
                    {"property": ref, "displayName": name} for ref, name in properties
                ],
            }
        ]
    }
    if next_token:
        payload["nextPageToken"] = next_token
    return payload


def streams(*uris: str) -> dict:
    return {
        "dataStreams": [
            {"name": f"{n}", "webStreamData": {"defaultUri": uri}} for n, uri in enumerate(uris)
        ]
    }


def client_for(handler) -> AnalyticsAdminHttpClient:
    return AnalyticsAdminHttpClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


# --- what counts as a match -------------------------------------------------


def test_a_property_whose_stream_is_the_site_is_bound() -> None:
    property_ = AnalyticsProperty("properties/1", "WordKit", ("wordkitapp.com",))
    assert property_matches_site(property_, "wordkitapp.com")


def test_a_domain_wide_stream_covers_its_own_subdomain() -> None:
    """Instrumenting example.com and verifying www.example.com is normal."""
    property_ = AnalyticsProperty("properties/1", "Site", ("example.com",))
    assert property_matches_site(property_, "www.example.com")


def test_a_subdomain_stream_does_not_claim_the_parent_domain() -> None:
    """Proving control of the blog must not attach the whole company's data."""
    property_ = AnalyticsProperty("properties/1", "Blog", ("blog.example.com",))
    assert not property_matches_site(property_, "example.com")


def test_a_lookalike_host_is_not_a_match() -> None:
    property_ = AnalyticsProperty("properties/1", "Other", ("notwordkitapp.com",))
    assert not property_matches_site(property_, "wordkitapp.com")
    # And the suffix check must not treat a bare suffix as a subdomain boundary.
    assert not property_matches_site(
        AnalyticsProperty("properties/1", "Other", ("kitapp.com",)), "wordkitapp.com"
    )


def test_a_property_with_no_readable_streams_matches_nothing() -> None:
    """The safe direction: unbound means refused, never accepted."""
    assert not property_matches_site(AnalyticsProperty("properties/1", "X", ()), "wordkitapp.com")


def test_a_stream_uri_must_be_a_web_address() -> None:
    assert stream_host("https://wordkitapp.com") == "wordkitapp.com"
    assert stream_host("wordkitapp.com") == "wordkitapp.com"
    assert stream_host("HTTPS://WordKitApp.com/app") == "wordkitapp.com"
    assert stream_host("ftp://wordkitapp.com") is None
    assert stream_host("") is None


# --- reading the account ----------------------------------------------------


@pytest.mark.asyncio
async def test_each_property_is_listed_with_the_hosts_it_collects_from() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accountSummaries"):
            return httpx.Response(200, json=summaries(("properties/1", "WordKit")))
        assert request.url.path == "/v1beta/properties/1/dataStreams"
        return httpx.Response(200, json=streams("https://wordkitapp.com"))

    async with (client := client_for(handler)).client:
        found = await client.list_properties("token")

    assert found == [AnalyticsProperty("properties/1", "WordKit", ("wordkitapp.com",))]


@pytest.mark.asyncio
async def test_a_property_whose_streams_are_forbidden_is_returned_unbound() -> None:
    """One unreadable property must not fail the whole listing.

    It comes back with no hosts, so `property_matches_site` refuses it, and the
    other properties in the account are still selectable.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accountSummaries"):
            return httpx.Response(200, json=summaries(("properties/1", "A"), ("properties/2", "B")))
        if request.url.path == "/v1beta/properties/1/dataStreams":
            return httpx.Response(403, json={"error": "forbidden"})
        return httpx.Response(200, json=streams("https://wordkitapp.com"))

    async with (client := client_for(handler)).client:
        found = await client.list_properties("token")

    assert found[0].stream_hosts == ()
    assert not property_matches_site(found[0], "wordkitapp.com")
    assert property_matches_site(found[1], "wordkitapp.com")


@pytest.mark.asyncio
async def test_more_than_one_page_of_accounts_is_followed() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accountSummaries"):
            token = request.url.params.get("pageToken")
            seen.append(token)
            if token is None:
                return httpx.Response(200, json=summaries(("properties/1", "A"), next_token="p2"))
            return httpx.Response(200, json=summaries(("properties/2", "B")))
        return httpx.Response(200, json=streams("https://wordkitapp.com"))

    async with (client := client_for(handler)).client:
        found = await client.list_properties("token")

    assert seen == [None, "p2"]
    assert [item.property_ref for item in found] == ["properties/1", "properties/2"]


@pytest.mark.asyncio
async def test_a_listing_that_never_stops_paging_is_bounded() -> None:
    """A cursor that always returns another page must not loop forever."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path.endswith("/accountSummaries"):
            calls += 1
            return httpx.Response(200, json=summaries(("properties/1", "A"), next_token="always"))
        return httpx.Response(200, json=streams("https://wordkitapp.com"))

    async with (client := client_for(handler)).client:
        await client.list_properties("token")

    assert calls == 10


@pytest.mark.asyncio
async def test_a_failed_listing_is_an_error_not_an_empty_account() -> None:
    """"No properties" and "we could not ask" must not look the same.

    Returning an empty list on a 500 would show the operator an account with
    nothing in it and invite them to conclude they authorized the wrong Google
    account.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    async with (client := client_for(handler)).client:
        with pytest.raises(GoogleAnalyticsError) as error:
            await client.list_properties("token")

    assert str(error.value) == "analytics_property_list_failed"


@pytest.mark.asyncio
async def test_a_summary_without_a_property_ref_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accountSummaries"):
            return httpx.Response(
                200,
                json={"accountSummaries": [{"propertySummaries": [{"displayName": "no ref"}]}]},
            )
        return httpx.Response(200, json=streams("https://wordkitapp.com"))

    async with (client := client_for(handler)).client:
        with pytest.raises(GoogleAnalyticsError) as error:
            await client.list_properties("token")

    assert str(error.value) == "analytics_property_response_invalid"


@pytest.mark.asyncio
async def test_a_property_with_no_web_stream_carries_no_hosts() -> None:
    """An app-only property measures no website, so it binds to no site."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accountSummaries"):
            return httpx.Response(200, json=summaries(("properties/1", "App only")))
        return httpx.Response(200, json={"dataStreams": [{"name": "1", "androidAppStreamData": {}}]})

    async with (client := client_for(handler)).client:
        found = await client.list_properties("token")

    assert found[0].stream_hosts == ()
