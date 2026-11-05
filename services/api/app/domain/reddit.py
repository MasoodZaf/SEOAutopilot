"""Rules for Reddit drafts that hold whatever the model or the person wrote.

Reddit communities ban accounts that self-promote without saying so, and post
the same link around. So every draft carries a disclosure line, a subreddit
gets at most one hand-off a week from a site, and a site gets at most three a
day. None of this calls Reddit: the person posts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

THREAD = re.compile(
    r"^https?://(?:www\.|old\.|new\.)?reddit\.com/r/([A-Za-z0-9_]{2,21})/comments/([a-z0-9]{3,12})(?:/([A-Za-z0-9_-]{0,120}))?",
    re.IGNORECASE,
)
SUBREDDIT = re.compile(r"^(?:/?r/)?([A-Za-z0-9_]{2,21})/?$")

SUBREDDIT_COOLDOWN = timedelta(days=7)
DAILY_HAND_OFFS = 3
# Reddit's submit page reads its fields from the URL. Long bodies are cut by
# browsers and proxies, so past this the text is copied instead.
PREFILL_LIMIT = 1800


@dataclass(frozen=True, slots=True)
class Thread:
    subreddit: str
    post_id: str
    url: str


def parse_thread(url: str) -> Thread | None:
    """A Reddit thread URL in canonical form, or None if it is not one."""
    match = THREAD.match(url.strip())
    if match is None:
        return None
    subreddit, post_id, slug = match.group(1), match.group(2).lower(), match.group(3) or ""
    canonical = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/" + (f"{slug}/" if slug else "")
    return Thread(subreddit, post_id, canonical)


def parse_subreddit(value: str) -> str | None:
    match = SUBREDDIT.match(value.strip())
    return match.group(1) if match else None


def title_from_slug(url: str) -> str:
    """The thread's title as Reddit put it in the URL, which is all we can see of it."""
    match = THREAD.match(url)
    slug = (match.group(3) if match else "") or ""
    return slug.replace("_", " ").strip()


def disclosure(site_name: str) -> str:
    return f"(Disclosure: I run {site_name}.)"


def with_disclosure(body: str, site_name: str) -> str:
    """The body, ending with a disclosure line unless it already discloses."""
    text = body.strip()
    lowered = text.lower()
    if "disclosure" in lowered or f"i run {site_name.lower()}" in lowered or "i made " in lowered:
        return text
    return f"{text}\n\n{disclosure(site_name)}"


def submit_url(subreddit: str, title: str, body: str) -> str:
    """Reddit's own submit page for a text post, with what fits filled in."""
    url = f"https://www.reddit.com/r/{subreddit}/submit?type=TEXT&title={quote(title[:300])}"
    if len(body) <= PREFILL_LIMIT:
        url += f"&text={quote(body)}"
    return url
