import pytest

from app.domain.sitemap_entry import add_sitemap_urls

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://thecalchive.com/emi-calculator</loc>
  </url>
</urlset>"""


def test_new_urls_go_before_the_close_and_nothing_else_changes():
    result = add_sitemap_urls(
        SITEMAP,
        ["https://thecalchive.com/blog/how-emi-works", "https://thecalchive.com/emi-calculator/"],
        "2026-11-04",
    )
    assert result is not None
    after, added = result
    assert added == ["https://thecalchive.com/blog/how-emi-works"]
    assert after.startswith(SITEMAP.split("</urlset>")[0])
    assert after.endswith(
        "  <url>\n    <loc>https://thecalchive.com/blog/how-emi-works</loc>\n"
        "    <lastmod>2026-11-04</lastmod>\n  </url>\n</urlset>"
    )


def test_nothing_to_add_and_sitemap_indexes_are_left_alone():
    assert add_sitemap_urls(SITEMAP, ["https://thecalchive.com/emi-calculator"], "2026-11-04") is None
    index = '<sitemapindex xmlns="x"><sitemap><loc>a</loc></sitemap></sitemapindex>'
    assert add_sitemap_urls(index, ["https://a.example/b"], "2026-11-04") is None


def test_urls_are_escaped():
    after, _ = add_sitemap_urls(SITEMAP, ["https://a.example/b?x=1&y=<2>"], "2026-11-04") or ("", [])
    assert "<loc>https://a.example/b?x=1&amp;y=&lt;2&gt;</loc>" in after


def test_lastmod_must_be_a_date():
    with pytest.raises(ValueError):
        add_sitemap_urls(SITEMAP, ["https://a.example/b"], "</lastmod><x>")
