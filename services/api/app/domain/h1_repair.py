"""Deterministic repair for the `content.title_h1_mismatch` finding.

The replacement heading is cut from the page's own `<title>`, which the crawler
already stores, so this needs no language model and returns the same heading for
the same title every time. Generation stays out of the change loop; H4 can add a
provider later for headings a title cannot supply.

Everything here refuses rather than guesses. A document with no `<h1>`, with more
than one, or whose title yields nothing usable produces no edit, because a
proposal that silently rewrites the wrong element is worse than no proposal.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# Separators a site uses to append its brand or a marketing phrase to the topic.
# Each must be surrounded by whitespace so a hyphenated word ("built-in") and a
# compound title ("Num2Words") survive intact. "|" is exempt: it is never part
# of a word.
_SEGMENT_PATTERN = re.compile(r"\s+[—–\-·»]\s+|\s*\|\s*")

_H1_PATTERN = re.compile(r"<h1(?P<attrs>\s[^>]*)?>(?P<inner>.*?)</h1\s*>", re.IGNORECASE | re.DOTALL)

MIN_HEADING_LENGTH = 3
MAX_HEADING_LENGTH = 70


class H1RepairError(Exception):
    """The document or the title does not support a safe, verifiable edit."""


@dataclass(frozen=True, slots=True)
class H1Repair:
    """One verified edit: what it replaced, with what, and the document after."""

    before_content: str
    after_content: str
    previous_heading: str
    heading: str


def derive_heading(title: str) -> str:
    """Return the topic segment of a title, or raise if it yields nothing usable.

    "EMI Calculator — Free Online Tool | CalcHive" -> "EMI Calculator"
    """
    candidate = _SEGMENT_PATTERN.split(title.strip(), maxsplit=1)[0].strip()
    if len(candidate) < MIN_HEADING_LENGTH or len(candidate) > MAX_HEADING_LENGTH:
        raise H1RepairError("h1_repair_title_yields_no_heading")
    if not re.search(r"[a-zA-Z0-9]{3,}", candidate):
        raise H1RepairError("h1_repair_title_yields_no_heading")
    # The finding is that title and heading share no keywords. A heading taken
    # verbatim from the title cannot fail that test under any tokenizer, so the
    # repair is checked against the title itself rather than against a copy of
    # the rule that produced the finding.
    if candidate.lower() not in title.lower():
        raise H1RepairError("h1_repair_heading_not_drawn_from_title")
    return candidate


def is_site_root(normalized_url: str) -> bool:
    """True when the URL addresses the site's front page."""
    path = urlsplit(normalized_url).path
    return path in {"", "/", "/index.html"}


def plan_repair(document: str, title: str, normalized_url: str) -> H1Repair:
    """Repair a page's H1, refusing the front page.

    A front page's H1 is its headline. It is written to introduce the site
    rather than to name a topic, it is the one place the shared heading is
    genuinely about the page, and on this layout it is the only page where the
    heading is visible. Rewriting it from the title would replace an editorial
    decision with a brand string, so the front page is left to a human.
    """
    if is_site_root(normalized_url):
        raise H1RepairError("h1_repair_refuses_site_root")
    return repair_document(document, title)


def repair_document(document: str, title: str) -> H1Repair:
    """Replace the document's single H1 with the topic drawn from its title.

    `title` is the page's title as the crawler parsed it: text, not markup.
    Entities are re-escaped on the way back into the document.
    """
    matches = list(_H1_PATTERN.finditer(document))
    if not matches:
        raise H1RepairError("h1_repair_no_h1_element")
    if len(matches) > 1:
        raise H1RepairError("h1_repair_multiple_h1_elements")

    heading = derive_heading(title)
    match = matches[0]
    previous = _visible_text(match.group("inner"))
    if not previous:
        raise H1RepairError("h1_repair_h1_is_empty")
    if previous == heading:
        raise H1RepairError("h1_repair_already_correct")

    start, end = match.span("inner")
    after = document[:start] + html.escape(heading, quote=False) + document[end:]

    # Prove the edit rather than trust the substitution: the document must be
    # byte-identical either side of the one span, and re-reading it must give
    # back exactly the heading intended.
    if after[:start] != document[:start] or after[len(after) - (len(document) - end) :] != document[end:]:
        raise H1RepairError("h1_repair_edit_escaped_its_span")
    rewritten = list(_H1_PATTERN.finditer(after))
    if len(rewritten) != 1 or _visible_text(rewritten[0].group("inner")) != heading:
        raise H1RepairError("h1_repair_verification_failed")

    return H1Repair(
        before_content=document,
        after_content=after,
        previous_heading=previous,
        heading=heading,
    )


def _visible_text(fragment: str) -> str:
    """Collapse an H1's inner markup to the text a reader would see."""
    without_breaks = re.sub(r"<br\s*/?>", " ", fragment, flags=re.IGNORECASE)
    without_tags = re.sub(r"<[^>]+>", "", without_breaks)
    return " ".join(html.unescape(without_tags).split())
