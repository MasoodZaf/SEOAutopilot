"""Insert reviewed direct answers under a page's question headings.

The answers were written by a model and then edited, fact-checked and accepted
by a person; this module only places them. Placing is deterministic: each
answer becomes a paragraph immediately after the one heading whose visible
text matches its question, and the page gains one FAQPage JSON-LD block built
here from those same question/answer pairs -- the model never writes markup.

Everything refuses rather than guesses. A heading that appears nowhere or more
than once, a page that already declares FAQ markup, or a file type this cannot
safely write into produces no edit, because an answer placed under the wrong
heading is worse than none.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

_HEADING = re.compile(
    r"<(?P<tag>h[23])(?P<attrs>\s[^>]*)?>(?P<inner>.*?)</(?P=tag)\s*>", re.IGNORECASE | re.DOTALL
)
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_NEXT_BLOCK = re.compile(r"<(?:p(?P<attrs>\s[^>]*)?>|h[1-6][\s>])", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_JSX_EXPRESSION = re.compile(r"\{[^{}]*\}")
HTML_SUFFIXES = (".html", ".htm")
JSX_SUFFIXES = (".jsx", ".tsx")
MAX_ITEMS = 5
MAX_ANSWER_CHARS = 600


class AnswerBlockError(ValueError):
    """The answers cannot be placed in this file; the message is a refusal code."""


@dataclass(frozen=True, slots=True)
class AnswerItem:
    heading: str
    answer: str


@dataclass(frozen=True, slots=True)
class AnswerBlockEdit:
    before: str
    after: str
    placed: int


def _visible(fragment: str) -> str:
    """A heading's text as the crawler reads it: a line break separates words,
    inline markup such as <em> does not. A JSX expression is not text at all,
    so a heading built from one never matches a crawled heading."""
    text = _JSX_EXPRESSION.sub("\0", fragment)
    text = _BREAK.sub(" ", text)
    text = _TAG.sub("", text)
    return " ".join(html.unescape(text).split()).casefold()


def _escape(text: str, jsx: bool) -> str:
    escaped = html.escape(" ".join(text.split()), quote=False)
    # Braces open an expression in JSX; as entities they are only characters.
    return escaped.replace("{", "&#123;").replace("}", "&#125;") if jsx else escaped


def _paragraph_attrs(document: str, index: int) -> str:
    """The attributes of the page's own next paragraph, so an inserted answer
    is styled like the copy around it. None when a heading comes first."""
    following = _NEXT_BLOCK.search(document, index)
    if following is None or following.group(0).lower().startswith("<h"):
        return ""
    return following.group("attrs") or ""


def _line_indent(document: str, index: int) -> str:
    start = document.rfind("\n", 0, index) + 1
    line = document[start:index]
    return line[: len(line) - len(line.lstrip())]


def faq_json_ld(items: list[AnswerItem]) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": " ".join(item.heading.split()),
                "acceptedAnswer": {"@type": "Answer", "text": " ".join(item.answer.split())},
            }
            for item in items
        ],
    }
    # "</" inside a script element would end it early.
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def place_answers(document: str, target_path: str, items: list[AnswerItem]) -> AnswerBlockEdit:
    path = target_path.lower()
    jsx = path.endswith(JSX_SUFFIXES)
    if not (jsx or path.endswith(HTML_SUFFIXES)):
        raise AnswerBlockError("answer_block_unsupported_file")
    if not items or len(items) > MAX_ITEMS:
        raise AnswerBlockError("answer_block_item_count_invalid")
    if any(not item.answer.strip() or len(item.answer) > MAX_ANSWER_CHARS for item in items):
        raise AnswerBlockError("answer_block_answer_invalid")
    if re.search(r"FAQPage", document):
        raise AnswerBlockError("page_already_has_faq_markup")

    matches = list(_HEADING.finditer(document))
    insertions: list[tuple[int, str]] = []
    for item in items:
        wanted = " ".join(item.heading.split()).casefold()
        found = [match for match in matches if _visible(match.group("inner")) == wanted]
        if not found:
            raise AnswerBlockError("answer_heading_not_found")
        if len(found) > 1:
            raise AnswerBlockError("answer_heading_ambiguous")
        heading = found[0]
        indent = _line_indent(document, heading.start())
        attrs = _paragraph_attrs(document, heading.end())
        insertions.append((heading.end(), f"\n{indent}<p{attrs}>{_escape(item.answer, jsx)}</p>"))

    markup = faq_json_ld(items)
    if jsx:
        # After the last answer, in the component's own markup: a script tag
        # in JSX needs its JSON passed as a string, not as children.
        last = max(insertions, key=lambda pair: pair[0])
        indent = _line_indent(document, last[0])
        script = (
            f"\n{indent}<script type=\"application/ld+json\" "
            f"dangerouslySetInnerHTML={{{{ __html: {json.dumps(markup)} }}}} />"
        )
        insertions.append((last[0], last[1] + script))
        insertions.remove(last)
    else:
        anchor = re.search(r"</head\s*>", document, re.IGNORECASE) or re.search(
            r"</body\s*>", document, re.IGNORECASE
        )
        if anchor is None:
            raise AnswerBlockError("answer_block_no_head_or_body")
        indent = _line_indent(document, anchor.start())
        insertions.append(
            (anchor.start(), f'  <script type="application/ld+json">{markup}</script>\n{indent}')
        )

    after = document
    for index, text in sorted(insertions, key=lambda pair: pair[0], reverse=True):
        after = after[:index] + text + after[index:]
    return AnswerBlockEdit(before=document, after=after, placed=len(items))
