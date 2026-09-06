"""Putting the word a page is about back into its title and heading.

`h1_repair` derives a heading *from* the title, so where a title already omitted
the subject the heading inherited the omission. On the pilot site that produced
31 unique, topical headings that still did not contain the word people type:
`/networth-calculator` titled "Net Worth Tracker", `/debt-calculator` titled
"Debt Payoff Planner", against a search cluster of "calculator debt" at position
86.

The URL is the evidence. A slug the author compounded deliberately --
`networth-calculator` -- says what the page is, and no template overwrites it.
So the repair is bounded by what the slug already claims and never invents a
subject of its own.

Two shapes, and the difference is not cosmetic:

  replace   The name ends in a word that means the same job by another name:
            "Debt Payoff Planner" -> "Debt Payoff Calculator". Appending would
            give "Debt Payoff Planner Calculator", which no one would write.

  append    The name is a subject with no such word: "Ideal Weight" ->
            "Ideal Weight Calculator".

Anything else is refused rather than guessed at. "Unit Converter" and
"Zodiac & Birth Chart" are not calculators wearing a different hat -- a
converter and a chart are their own search terms -- so this leaves them alone
and says why.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.domain.h1_repair import (
    MAX_HEADING_LENGTH,
    MIN_HEADING_LENGTH,
    _visible_text,
    is_site_root,
)

_TITLE_PATTERN = re.compile(r"<title(?P<attrs>\s[^>]*)?>(?P<inner>.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_H1_PATTERN = re.compile(r"<h1(?P<attrs>\s[^>]*)?>(?P<inner>.*?)</h1\s*>", re.IGNORECASE | re.DOTALL)
# The separator between a page's name and the site's boilerplate suffix.
_SUFFIX_PATTERN = re.compile(r"\s+[—–\-·»]\s+|\s*\|\s*")

MAX_TITLE_LENGTH = 65

# Words that name the same job as "calculator" for a page whose slug says
# calculator. Replacing one keeps the title readable; appending after one does
# not. Deliberately excludes "converter" and "chart": those are subjects people
# search for in their own right, and overwriting them would trade a real term
# for a guess.
_INTERCHANGEABLE = frozenset({"tracker", "planner", "quiz", "checker", "estimator", "counter", "tool"})


@dataclass(frozen=True, slots=True)
class TitleRepair:
    """One edit spanning the title and the heading, or a refusal naming why."""

    applied: bool
    reason: str
    before: str = ""
    after: str = ""
    title_before: str = ""
    title_after: str = ""
    heading_before: str = ""
    heading_after: str = ""


def slug_topic(normalized_url: str) -> str | None:
    """The subject word a compound slug claims, or nothing.

    Mirrors the worker rule that raises the finding: only the last path
    segment, only when the author compounded it, and only the trailing word,
    which is the noun in every slug shape this is meant for.
    """
    path = urlsplit(normalized_url).path.strip("/")
    if not path:
        return None
    segment = path.rsplit("/", 1)[-1]
    for extension in (".html", ".htm", ".php"):
        segment = segment.removesuffix(extension)
    parts = [word for word in re.split(r"[^a-z0-9]+", segment.lower()) if word]
    if len(parts) < 2 or len(parts[-1]) < 4:
        return None
    return parts[-1]


def compose_name(name: str, topic: str) -> tuple[str, str] | None:
    """The rewritten name and how it was reached, or nothing if it is not safe."""
    words = name.split()
    if not words:
        return None
    if any(word.lower().strip(".,;:!?") == topic for word in words):
        return None
    if words[-1].lower().strip(".,;:!?") in _INTERCHANGEABLE:
        return " ".join(words[:-1] + [topic.capitalize()]), "replaced"
    return f"{name} {topic.capitalize()}", "appended"


def _one_match(pattern: re.Pattern[str], document: str) -> re.Match[str] | None:
    found = pattern.findall(document)
    if len(found) != 1:
        return None
    return pattern.search(document)


def plan_repair(document: str, normalized_url: str) -> TitleRepair:
    """Decide the edit without making it. Every refusal names itself."""
    if is_site_root(normalized_url):
        # The front page's name is a brand decision, not a slug's to make.
        return TitleRepair(False, "title_repair_refuses_site_root")

    topic = slug_topic(normalized_url)
    if topic is None:
        return TitleRepair(False, "title_repair_slug_claims_no_topic")

    title_match = _one_match(_TITLE_PATTERN, document)
    if title_match is None:
        return TitleRepair(False, "title_repair_needs_exactly_one_title")
    heading_match = _one_match(_H1_PATTERN, document)
    if heading_match is None:
        return TitleRepair(False, "title_repair_needs_exactly_one_h1")

    title = _visible_text(title_match.group("inner")).strip()
    heading = _visible_text(heading_match.group("inner")).strip()
    if not title or not heading:
        return TitleRepair(False, "title_repair_title_or_h1_is_empty")

    # The name is everything before the site's boilerplate suffix; the suffix
    # is kept verbatim, separator included, so it survives the edit untouched.
    name = _SUFFIX_PATTERN.split(title, 1)[0]
    suffix = title[len(name) :]
    name = name.strip()
    if heading != name:
        # This repair rewrites both spans as one change, which is only sound
        # while they say the same thing. A page whose heading has diverged from
        # its title needs a person, not a rule.
        return TitleRepair(False, "title_repair_h1_does_not_match_title_name")

    composed = compose_name(name, topic)
    if composed is None:
        return TitleRepair(False, "title_repair_topic_already_present")
    new_name, how = composed

    if not MIN_HEADING_LENGTH <= len(new_name) <= MAX_HEADING_LENGTH:
        return TitleRepair(False, "title_repair_heading_out_of_range")
    new_title = f"{new_name}{suffix}"
    if len(new_title) > MAX_TITLE_LENGTH:
        # A title search engines will truncate is not an improvement on one
        # that merely omits a word.
        return TitleRepair(False, "title_repair_title_too_long")

    return TitleRepair(
        applied=True,
        reason=f"title_repair_{how}",
        title_before=title,
        title_after=new_title,
        heading_before=heading,
        heading_after=new_name,
    )


def repair_document(document: str, normalized_url: str) -> TitleRepair:
    """Apply the planned edit, proving nothing outside the two spans moved."""
    plan = plan_repair(document, normalized_url)
    if not plan.applied:
        return plan

    title_match = _one_match(_TITLE_PATTERN, document)
    heading_match = _one_match(_H1_PATTERN, document)
    if title_match is None or heading_match is None:  # pragma: no cover - plan checked
        return TitleRepair(False, "title_repair_verification_failed")

    edited = document
    # Later span first, so the earlier span's offsets stay valid.
    for match, replacement in sorted(
        ((title_match, plan.title_after), (heading_match, plan.heading_after)),
        key=lambda pair: pair[0].start(),
        reverse=True,
    ):
        tag = "title" if match is title_match else "h1"
        attrs = match.group("attrs") or ""
        edited = (
            edited[: match.start()]
            + f"<{tag}{attrs}>{_escape(replacement)}</{tag}>"
            + edited[match.end() :]
        )

    # Everything before the first edited span and after the last must be
    # untouched. The mid-document region between them is checked by replacing
    # only the matched spans above, but assert the ends explicitly: a regex
    # that swallowed more than its element would show up here and nowhere else.
    first, last = sorted((title_match, heading_match), key=lambda m: m.start())
    if not edited.startswith(document[: first.start()]):
        return TitleRepair(False, "title_repair_edit_escaped_its_span")
    if not edited.endswith(document[last.end() :]):
        return TitleRepair(False, "title_repair_edit_escaped_its_span")

    verified = plan_repair(edited, normalized_url)
    if verified.applied or verified.reason != "title_repair_topic_already_present":
        # After a correct edit the topic is present, so the same planner must
        # now refuse for that reason and no other.
        return TitleRepair(False, "title_repair_verification_failed")

    return TitleRepair(
        applied=True,
        reason=plan.reason,
        before=document,
        after=edited,
        title_before=plan.title_before,
        title_after=plan.title_after,
        heading_before=plan.heading_before,
        heading_after=plan.heading_after,
    )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
