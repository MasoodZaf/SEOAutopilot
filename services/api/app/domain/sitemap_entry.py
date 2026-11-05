"""Add page URLs to a site's sitemap.xml without touching anything else in it."""

from __future__ import annotations

import html
import re

URLSET_CLOSE = re.compile(r"</urlset\s*>", re.IGNORECASE)
LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def add_sitemap_urls(sitemap: str, urls: list[str], lastmod: str) -> tuple[str, list[str]] | None:
    """The sitemap with each URL it does not list yet, and those URLs; None if none to add.

    Refuses anything that is not a plain <urlset> (a sitemap index, say): that
    is the site's own structure to change.
    """
    if not DATE.match(lastmod):
        raise ValueError("lastmod must be YYYY-MM-DD")
    closes = list(URLSET_CLOSE.finditer(sitemap))
    if len(closes) != 1 or "<sitemapindex" in sitemap.lower():
        return None
    listed = {html.unescape(match.group(1)).rstrip("/") for match in LOC.finditer(sitemap)}
    added: list[str] = []
    for url in urls:
        if url.rstrip("/") not in listed and url not in added:
            added.append(url)
    if not added:
        return None
    entries = "".join(
        "  <url>\n"
        f"    <loc>{html.escape(url, quote=False)}</loc>\n"
        f"    <lastmod>{lastmod}</lastmod>\n"
        "  </url>\n"
        for url in added
    )
    close = closes[0]
    before = sitemap[: close.start()]
    if not before.endswith("\n"):
        before += "\n"
    return before + entries + sitemap[close.start():], added
