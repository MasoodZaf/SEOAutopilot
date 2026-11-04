"""Topics a person asks a post to be written about.

A post on a topic one of the site's pages already targets competes with that
page in search, so such a topic is refused and the page is named instead. The
test is deliberately plain: a page covers a topic when its title, or the words
in its URL, contain every meaningful word of the topic. "EMI calculator" is
covered by "EMI Calculator — Free Online Tool"; "how EMI is calculated" is not.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "how", "i", "in", "is", "it", "my", "of", "on", "or", "should", "the", "to", "vs", "what", "when", "where", "which", "who", "why", "will", "with", "you", "your"]
)
TOPIC_MIN, TOPIC_MAX = 3, 120


def topic_words(text: str) -> set[str]:
    return {word for word in WORD.findall(text.lower()) if word not in STOPWORDS}


@dataclass(frozen=True)
class SitePage:
    url: str
    title: str


def covering_page(topic: str, pages: list[SitePage]) -> SitePage | None:
    """The first page whose title or URL already has every word of the topic."""
    wanted = topic_words(topic)
    if not wanted:
        return None
    for page in pages:
        have = topic_words(page.title) | topic_words(page.url.replace("-", " ").replace("_", " "))
        if wanted <= have:
            return page
    return None


def topic_sections(topic: str, intent: str) -> list[dict[str, Any]]:
    """The plan a topic brief carries, in the shape analysis briefs use."""
    words = sorted(topic_words(topic))
    return [
        {
            "key": "target",
            "title": "Target and intent",
            "finding": (
                f"A person on this workspace asked for a post on '{topic}'. No page on the site "
                "targets it, and there is no search data for it yet, so this is a bet rather "
                "than a measured gap."
            ),
            "recommendation": (
                f"Write a post that answers '{topic}' for {intent} intent, and link to the "
                "site's related pages where they help the reader."
            ),
            "evidence": {"source": "topic", "intent": intent},
        },
        {
            "key": "on_page",
            "title": "On-page essentials",
            "finding": "A new page starts with no title, description, or heading evidence.",
            "recommendation": (
                "Write a 15-65 character title and a 50-170 character description that both "
                f"use {', '.join(words) or 'the topic words'}, and give the page a single H1."
            ),
            "evidence": {"required_tokens": words},
        },
    ]


def content_hash(sections: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(sections, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
