"""A blog post as an HTML page in the site's own shell, for sites that are plain HTML.

A site built from hand-written HTML files serves no Markdown, so a post is
published as a copy of one of the site's own pages (its head, styles, header
and footer) with only the `<main>` element replaced by the post.

Everything the model wrote is escaped: the Markdown renderer here supports a
small fixed subset and emits no raw HTML from the draft. The page carries no
canonical, robots or refresh directive, so the post's own URL is its
canonical and the change stays an ordinary new page for review.
"""

from __future__ import annotations

import html
import json
import re
from posixpath import normpath
from urllib.parse import urljoin, urlsplit

MAIN = re.compile(r"(<main\b[^>]*>)(.*?)(</main\s*>)", re.IGNORECASE | re.DOTALL)
TITLE = re.compile(r"<title\b[^>]*>.*?</title\s*>", re.IGNORECASE | re.DOTALL)
HEAD_CLOSE = re.compile(r"</head\s*>", re.IGNORECASE)
JSON_LD = re.compile(
    r"<script\b[^>]*\btype\s*=\s*[\"']application/ld\+json[\"'][^>]*>.*?</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Tags that describe the template page rather than the post, or that decide
# indexing. They are removed and, where the post needs one, written afresh.
PAGE_SPECIFIC = [
    re.compile(r"<link\b[^>]*\brel\s*=\s*[\"']?\s*canonical\b[^>]*>", re.IGNORECASE),
    re.compile(r"<link\b[^>]*\brel\s*=\s*[\"']?\s*alternate\b[^>]*>", re.IGNORECASE),
    re.compile(r"<meta\b[^>]*\bname\s*=\s*[\"']?\s*(?:robots|googlebot|description)\b[^>]*>", re.IGNORECASE),
    re.compile(r"<meta\b[^>]*\bhttp-equiv\s*=\s*[\"']?\s*refresh\b[^>]*>", re.IGNORECASE),
    re.compile(r"<meta\b[^>]*\b(?:property|name)\s*=\s*[\"']?\s*(?:og|twitter|article):[^>]*>", re.IGNORECASE),
]
URL_ATTRIBUTE = re.compile(r"(\s(?:href|src|action|poster)\s*=\s*)([\"'])(.*?)\2", re.IGNORECASE | re.DOTALL)
NOINDEX = re.compile(
    r"<meta\b[^>]*\bname\s*=\s*[\"']?\s*robots\b[^>]*\bnoindex\b[^>]*>", re.IGNORECASE
)


class BlogTemplateError(ValueError):
    """The chosen template page cannot hold a post; the code says why."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_post_page(
    template: str,
    *,
    template_url: str,
    post_url: str,
    title: str,
    description: str,
    author: str,
    published_on: str,
    body_markdown: str,
) -> str:
    """The template page with the post in place of its main content."""
    mains = MAIN.findall(template)
    if len(mains) != 1:
        raise BlogTemplateError("blog_template_needs_one_main_element")
    if not HEAD_CLOSE.search(template) or not TITLE.search(template):
        raise BlogTemplateError("blog_template_needs_head_and_title")
    if NOINDEX.search(template):
        raise BlogTemplateError("blog_template_is_noindex")

    page = JSON_LD.sub("", template)
    for pattern in PAGE_SPECIFIC:
        page = pattern.sub("", page)
    page = _absolute_urls(page, template_url)

    page = TITLE.sub(lambda _: f"<title>{html.escape(title)}</title>", page, count=1)
    schema = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": title,
            "description": description,
            "author": {"@type": "Person", "name": author},
            "datePublished": published_on,
            "mainEntityOfPage": post_url,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    head = (
        f'<meta name="description" content="{html.escape(description, quote=True)}">\n'
        f'<meta property="og:type" content="article">\n'
        f'<meta property="og:title" content="{html.escape(title, quote=True)}">\n'
        f'<meta property="og:url" content="{html.escape(post_url, quote=True)}">\n'
        f'<script type="application/ld+json">{schema}</script>\n'
    )
    page = HEAD_CLOSE.sub(lambda match: head + match.group(0), page, count=1)

    article = (
        "\n<article>\n"
        f"<h1>{html.escape(title)}</h1>\n"
        f"<p><small>By {html.escape(author)} · "
        f'<time datetime="{html.escape(published_on, quote=True)}">{html.escape(published_on)}</time>'
        "</small></p>\n"
        f"{render_markdown(body_markdown)}\n"
        "</article>\n"
    )
    return MAIN.sub(lambda match: match.group(1) + article + match.group(3), page, count=1)


def _absolute_urls(page: str, template_url: str) -> str:
    """Relative links resolved against the template's URL, so they work from any folder."""
    base = urlsplit(template_url).path or "/"

    def resolve(match: re.Match[str]) -> str:
        value = match.group(3).strip()
        lowered = value.lower()
        if not value or lowered.startswith(("/", "#", "http:", "https:", "mailto:", "tel:", "data:", "javascript:", "{")):
            return match.group(0)
        joined = urljoin(base, value)
        path, _, rest = joined.partition("?")
        resolved = normpath(path) + ("/" if path.endswith("/") and path != "/" else "")
        if not resolved.startswith("/"):
            resolved = "/" + resolved
        return f"{match.group(1)}{match.group(2)}{resolved}{'?' + rest if rest else ''}{match.group(2)}"

    return URL_ATTRIBUTE.sub(resolve, page)


# --- Markdown: a fixed, escaped subset -------------------------------------

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
BULLET = re.compile(r"^[-*+]\s+(.*)$")
NUMBERED = re.compile(r"^\d+[.)]\s+(.*)$")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*(.+?)\*\*")
ITALIC = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
CODE = re.compile(r"`([^`]+)`")


def render_markdown(markdown: str) -> str:
    """Headings, paragraphs, lists, quotes, links, bold, italic and code. Nothing else."""
    blocks: list[str] = []
    lines = markdown.replace("\r\n", "\n").split("\n")
    paragraph: list[str] = []
    items: list[str] = []
    list_tag = ""
    quote: list[str] = []

    def flush() -> None:
        nonlocal paragraph, items, list_tag, quote
        if paragraph:
            blocks.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph = []
        if items:
            blocks.append(f"<{list_tag}>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + f"</{list_tag}>")
            items, list_tag = [], ""
        if quote:
            blocks.append(f"<blockquote><p>{_inline(' '.join(quote))}</p></blockquote>")
            quote = []

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
            continue
        heading = HEADING.match(line)
        bullet = BULLET.match(line)
        numbered = NUMBERED.match(line)
        if heading:
            flush()
            # The post title is the page's only h1.
            level = min(max(len(heading.group(1)), 2), 4)
            blocks.append(f"<h{level}>{_inline(heading.group(2).strip('# '))}</h{level}>")
        elif bullet or numbered:
            tag = "ul" if bullet else "ol"
            if paragraph or quote or (items and list_tag != tag):
                flush()
            list_tag = tag
            items.append((bullet or numbered).group(1))  # type: ignore[union-attr]
        elif line.startswith(">"):
            if paragraph or items:
                flush()
            quote.append(line.lstrip("> ").strip())
        else:
            if items or quote:
                flush()
            paragraph.append(line)
    flush()
    return "\n".join(blocks)


def _safe_href(url: str) -> str | None:
    if url.startswith("/") and not url.startswith("//"):
        return url
    parts = urlsplit(url)
    if parts.scheme in {"http", "https"} and parts.netloc:
        return url
    return None


def _inline(text: str) -> str:
    """Escape everything, then add back the few inline marks the subset allows."""
    placeholders: list[str] = []

    def keep(fragment: str) -> str:
        placeholders.append(fragment)
        return f"\x00{len(placeholders) - 1}\x00"

    def link(match: re.Match[str]) -> str:
        href = _safe_href(match.group(2))
        label = html.escape(match.group(1))
        if href is None:
            return keep(label)
        return keep(f'<a href="{html.escape(href, quote=True)}">{label}</a>')

    text = CODE.sub(lambda match: keep(f"<code>{html.escape(match.group(1))}</code>"), text)
    text = LINK.sub(link, text)
    text = html.escape(text)
    text = BOLD.sub(r"<strong>\1</strong>", text)
    text = ITALIC.sub(r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda match: placeholders[int(match.group(1))], text)
