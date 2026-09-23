"""Turning a model's answer into a reviewable draft.

The model is asked to list its own factual claims. That is useful and not
sufficient -- a model that invents a statistic does not reliably also report
inventing it -- so the flags a reviewer must clear come from two places: what
the model declared, and what these checks find regardless of what it declared.

Every flag must be resolved by a person before the draft can be submitted.
"""

from __future__ import annotations

import re
from typing import Any

from app.content_drafts.prompt import SitePage

SLUG = re.compile(r"[^a-z0-9]+")
# A figure a reader might rely on: a percentage, money, a count with a unit, a
# year. Ordinals in headings ("Step 2") and list numbering are not figures.
FIGURE = re.compile(
    r"(?<![\w.])(?:[$£€₹]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?%|\b(?:19|20)\d{2}\b|"
    r"\d[\d,]*(?:\.\d+)?\s?(?:percent|million|billion|thousand|lakh|crore|years?|months?|days?))",
    re.IGNORECASE,
)
MARKDOWN_LINK = re.compile(r"\]\((/[^)\s]*)\)")
WORD = re.compile(r"[a-z0-9]+")
STOP = {"a", "an", "and", "the", "of", "to", "in", "for", "on", "how", "what", "is", "your", "with"}


class DraftRejected(ValueError):
    """The model's answer cannot become a draft."""


def slugify(value: str) -> str:
    slug = SLUG.sub("-", value.lower()).strip("-")
    return slug[:60].rstrip("-") or "post"


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start))
    right_candidates = [i for i in (text.find(". ", end), text.find("\n", end)) if i != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1 : right + 1].strip()[:300]


def _tokens(text: str) -> set[str]:
    return {token for token in WORD.findall(text.lower()) if token not in STOP}


def normalize(answer: dict[str, Any]) -> dict[str, Any]:
    """The model's answer, validated and trimmed to what the database accepts."""
    try:
        title = str(answer["title"]).strip()
        body = str(answer["body_markdown"]).strip()
        description = str(answer["meta_description"]).strip()
    except (KeyError, TypeError) as error:
        raise DraftRejected("draft_invalid") from error
    if len(title) < 5 or len(body) < 200:
        raise DraftRejected("draft_too_short")
    claims = [
        {"text": str(item.get("text", "")).strip()[:300], "verify": str(item.get("verify", "")).strip()[:300]}
        for item in answer.get("claims") or []
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ][:40]
    links = [str(link) for link in answer.get("internal_links") or [] if isinstance(link, str)][:40]
    return {
        "title": title[:200],
        "slug": slugify(str(answer.get("slug") or title)),
        "meta_description": description[:320],
        "body_markdown": body[:60000],
        "claims": claims,
        "internal_links": links,
    }


def review_flags(draft: dict[str, Any], pages: list[SitePage]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []

    def add(kind: str, text: str, detail: str) -> None:
        flags.append(
            {
                "id": f"{kind}-{len(flags) + 1}",
                "kind": kind,
                "text": text,
                "detail": detail,
                "resolved": False,
                "resolution": None,
            }
        )

    body: str = draft["body_markdown"]
    declared = " ".join(claim["text"] for claim in draft["claims"])
    for claim in draft["claims"]:
        add("claim", claim["text"], claim["verify"] or "Check this against a reliable source.")

    # Figures the model did not declare. One flag per sentence, not per match.
    seen: set[str] = set()
    for match in FIGURE.finditer(body):
        if match.group(0) in declared:
            continue
        sentence = _sentence_around(body, match.start(), match.end())
        if sentence in seen:
            continue
        seen.add(sentence)
        add(
            "figure",
            sentence,
            f"Contains the figure “{match.group(0).strip()}”, which the draft did not list as a claim.",
        )

    known_paths = {page.path for page in pages}
    for path in sorted(set(MARKDOWN_LINK.findall(body))):
        if path not in known_paths:
            add("link", path, "Links to a path that is not among the site's crawled pages.")
    if pages and not MARKDOWN_LINK.search(body):
        add("link", "(none)", "The draft links to no existing page. Add links a reader would use, or note why none fit.")

    title_tokens = _tokens(draft["title"])
    for page in pages:
        page_tokens = _tokens(page.title)
        if not title_tokens or not page_tokens:
            continue
        overlap = len(title_tokens & page_tokens) / len(title_tokens | page_tokens)
        if overlap >= 0.6:
            add(
                "overlap",
                page.path,
                f"An existing page, “{page.title[:120]}”, covers much the same topic. "
                "Make sure the new post answers something it does not, or improve that page instead.",
            )
    return flags[:60]
