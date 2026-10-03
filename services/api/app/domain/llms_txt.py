"""Render an llms.txt from a site's own crawl evidence.

Deterministic, with no model involved: the file lists the pages the last crawl
found indexable, grouped by their first path segment, each with the title and
meta description the site already publishes (format per llmstxt.org). Page
text is untrusted, so every field is flattened to one line and stripped of the
characters that would break out of a Markdown link or list item.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

MAX_LINKS = 150
MAX_TITLE = 120
MAX_DESCRIPTION = 200
ROOT_SECTION = "Pages"

_UNSAFE = re.compile(r"[\[\]`<>|*_#]")


@dataclass(frozen=True, slots=True)
class LlmsPage:
    url: str
    title: str | None
    description: str | None


def _clean(value: str | None, limit: int) -> str:
    flat = " ".join(_UNSAFE.sub(" ", value or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-") + "…"


def _link_target(url: str) -> str:
    # Parentheses and spaces end a Markdown link target; encode them.
    return quote(url, safe=":/?&=%~.-+,;@!$'")


def _section(path: str) -> str:
    segments = [part for part in path.split("/") if part]
    # Top-level pages (/, /about) sit together; deeper ones group by folder.
    if len(segments) < 2:
        return ROOT_SECTION
    segment = segments[0]
    return " ".join(segment.replace("-", " ").replace("_", " ").split()).title() or ROOT_SECTION


def render_llms_txt(site_name: str, origin: str, pages: Sequence[LlmsPage]) -> str:
    """The file body. Deterministic for the same inputs, so a rerun is a no-op diff."""
    home = origin.rstrip("/") + "/"
    ordered = sorted(pages, key=lambda page: (page.url.rstrip("/") != home.rstrip("/"), page.url))
    homepage = next((page for page in ordered if page.url.rstrip("/") == home.rstrip("/")), None)
    listed = [page for page in ordered if _clean(page.title, MAX_TITLE)][:MAX_LINKS]

    sections: dict[str, list[str]] = {}
    for page in listed:
        title = _clean(page.title, MAX_TITLE)
        description = _clean(page.description, MAX_DESCRIPTION)
        line = f"- [{title}]({_link_target(page.url)})" + (f": {description}" if description else "")
        sections.setdefault(_section(urlsplit(page.url).path), []).append(line)

    name = _clean(site_name, MAX_TITLE) or urlsplit(origin).hostname or "Site"
    out = [f"# {name}", ""]
    summary = _clean(homepage.description if homepage else None, MAX_DESCRIPTION)
    if summary:
        out += [f"> {summary}", ""]
    # The root section first, the rest alphabetically, so reruns do not reorder.
    for heading in sorted(sections, key=lambda key: (key != ROOT_SECTION, key)):
        out += [f"## {heading}", "", *sections[heading], ""]
    return "\n".join(out).rstrip("\n") + "\n"
