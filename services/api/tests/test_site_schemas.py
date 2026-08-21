import pytest
from pydantic import ValidationError

from app.api.schemas import SiteCreate


def test_origin_is_normalized() -> None:
    site = SiteCreate(name="Example", canonical_origin="HTTPS://Example.COM/")
    assert site.canonical_origin == "https://example.com"


@pytest.mark.parametrize(
    "origin",
    [
        "ftp://example.com",
        "https://user:pass@example.com",
        "https://example.com/private",
        "https://example.com?token=secret",
        "http://localhost",
        "https://example.local",
        "https://example.com:8443",
    ],
)
def test_unsafe_or_non_origin_values_are_rejected(origin: str) -> None:
    with pytest.raises(ValidationError):
        SiteCreate(name="Unsafe", canonical_origin=origin)
