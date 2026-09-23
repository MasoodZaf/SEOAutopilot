import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.keywords.cluster import similarity_tokens


@dataclass(frozen=True, slots=True)
class PageEvidence:
    status: int | None
    title: str | None
    meta_description: str | None
    h1: Sequence[str]
    word_count: int
    canonical_url: str | None
    robots_directives: Sequence[str]
    structured_data: Sequence[object] = field(default_factory=list)
    # How many pages in this crawl carry the same H1 text, this one included.
    # An H1 is only meaningful relative to the rest of the site, so the count
    # has to be measured across the crawl and handed in.
    pages_sharing_h1: int = 1
    # Words in the server's HTML before any script ran, when the crawler had to
    # render the page to see it. None means it was not rendered.
    server_word_count: int | None = None


@dataclass(frozen=True, slots=True)
class LinkGraphEvidence:
    inbound_internal_links: int = 0
    outbound_internal_links: int = 0
    inbound_broken_links: int = 0
    crawl_depth: int = 0
    missing_anchor_inbound_count: int = 0


@dataclass(frozen=True, slots=True)
class SearchConsoleEvidence:
    clicks: float = 0.0
    impressions: float = 0.0
    ctr: float | None = None
    position: float | None = None
    striking_distance_queries: int = 0
    low_ctr_queries: int = 0


@dataclass(frozen=True, slots=True)
class PerformanceEvidence:
    performance_score: int | None = None
    lcp_ms: float | None = None
    inp_ms: float | None = None
    cls: float | None = None
    ttfb_ms: float | None = None


@dataclass(frozen=True, slots=True)
class MultiAgentPageEvidence:
    page_id: str
    normalized_url: str
    page: PageEvidence
    link_graph: LinkGraphEvidence = field(default_factory=LinkGraphEvidence)
    search_console: SearchConsoleEvidence = field(default_factory=SearchConsoleEvidence)
    performance: PerformanceEvidence = field(default_factory=PerformanceEvidence)


def url_topic_tokens(normalized_url: str) -> list[tuple[str, str]]:
    """The subject a URL's last path segment claims for the page.

    Only the final path segment, because that names the page rather than its
    section, and within it only the trailing word, which is the noun: the
    modifiers in front of it are routinely concatenated in a slug and separated
    in prose, so `/networth-calculator` against "Net Worth Calculator" would
    report a missing "networth" that is plainly there.

    That also keeps this rule and `title_repair` describing the same thing. A
    finding the repair could never act on is a finding that only ever produces
    a refusal.
    """
    path = urlsplit(normalized_url).path.strip("/")
    if not path:
        return []
    segment = path.rsplit("/", 1)[-1]
    for extension in (".html", ".htm", ".php"):
        segment = segment.removesuffix(extension)
    parts = [word for word in re.split(r"[^a-z0-9]+", segment.lower()) if word]
    if len(parts) < 2:
        # A single-word segment names a section, not a subject: `/about`
        # titled "Our Story" is fine, and reporting it would bury the cases
        # where an author compounded a slug precisely to say what a page is.
        return []
    trailing = parts[-1]
    if len(trailing) < MIN_URL_TOPIC_LENGTH or trailing in URL_TOPIC_STOPWORDS:
        return []
    # Stemmed to match the tokens the title and heading are compared with, so
    # "calculator" in the slug matches "Calculators" in a title. The word the
    # author actually wrote is carried alongside it: a finding that tells
    # somebody their page omits "calculat" reads like a bug in the auditor.
    return [(stem, trailing) for stem in similarity_tokens(trailing)]


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: str
    summary: str
    impact: float
    confidence: float
    urgency: float
    effort: float
    risk: str
    category: str = "technical"


DEDUCTIONS = {"critical": 30, "high": 20, "medium": 10, "low": 5, "info": 0}
RISK_ADJUSTMENT = {"low": 1.0, "medium": 0.75, "high": 0.35, "prohibited": 0.0}


def opportunity_score(finding: Finding) -> float:
    raw = (
        finding.impact
        * finding.confidence
        * finding.urgency
        / (0.25 + 0.75 * finding.effort)
    )
    return round(min(100.0, raw * 100 * RISK_ADJUSTMENT[finding.risk]), 2)


# One page repeating another's H1 is a coincidence; three or more sharing a
# heading means the heading belongs to a template rather than to any page.
DUPLICATE_H1_PAGE_THRESHOLD = 3

# Words a URL slug uses to say what a page is. A slug is the one piece of
# evidence about a page's subject that the author chose deliberately and that no
# template can overwrite, so a slug saying "calculator" while the title and
# heading say neither is a mismatch worth reporting without needing any search
# data to prove it.
URL_TOPIC_STOPWORDS = frozenset(
    {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with", "index", "html", "php"}
)
MIN_URL_TOPIC_LENGTH = 4

# A page whose content only appears once JavaScript runs. Google renders such
# pages, but in a later pass and only if every script succeeds for its
# renderer; crawlers that do not run scripts -- the ones behind AI answer
# engines among them -- see only the shell. The floor keeps a page that is
# short in both forms out of it, and the share keeps out a page whose server
# HTML carries the substance and whose scripts only add to it.
JS_ONLY_MIN_RENDERED_WORDS = 100
JS_ONLY_MAX_SERVER_SHARE = 0.2


def content_requires_javascript(page: PageEvidence) -> bool:
    if page.server_word_count is None or page.word_count < JS_ONLY_MIN_RENDERED_WORDS:
        return False
    return page.server_word_count < page.word_count * JS_ONLY_MAX_SERVER_SHARE


def tokenize_text(text: str) -> set[str]:
    return set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", text.lower()))


def evaluate_page(evidence: PageEvidence) -> tuple[int, list[Finding]]:
    """Legacy/Single-page technical evaluation."""
    findings: list[Finding] = []

    def add(
        code: str,
        severity: str,
        summary: str,
        factors: tuple[float, float, float, float, str],
        category: str = "technical",
    ) -> None:
        findings.append(Finding(code, severity, summary, *factors, category=category))

    if evidence.status is None:
        add("status.missing", "high", "No HTTP status was recorded.", (0.60, 0.70, 0.70, 0.60, "low"))
    elif evidence.status >= 500:
        add("status.server_error", "critical", "The page returned a server error.", (0.95, 0.99, 1.00, 0.80, "low"))
    elif evidence.status >= 400:
        add("status.client_error", "high", "The page returned a client error.", (0.80, 0.99, 0.90, 0.65, "low"))
    elif 300 <= evidence.status < 400:
        add("status.redirect", "medium", "The crawled URL returned a redirect.", (0.45, 0.99, 0.60, 0.45, "low"))

    title = (evidence.title or "").strip()
    if not title:
        add("title.missing", "high", "The page has no title element.", (0.55, 0.98, 0.75, 0.25, "low"))
    elif len(title) < 15 or len(title) > 65:
        add("title.length", "low", "The title is outside the 15–65 character review range.", (0.25, 0.90, 0.40, 0.20, "low"))

    description = (evidence.meta_description or "").strip()
    if not description:
        add("description.missing", "medium", "The page has no meta description.", (0.40, 0.98, 0.65, 0.20, "low"))
    elif len(description) < 50 or len(description) > 170:
        add("description.length", "low", "The meta description is outside the 50–170 character review range.", (0.25, 0.90, 0.40, 0.20, "low"))

    if not evidence.h1:
        add("h1.missing", "medium", "The page has no H1 heading.", (0.35, 0.98, 0.55, 0.25, "low"), category="content")
    elif len(evidence.h1) > 1:
        add("h1.multiple", "low", "The page has multiple H1 headings.", (0.25, 0.95, 0.40, 0.25, "low"), category="content")
    if evidence.canonical_url is None:
        add("canonical.missing", "low", "The page has no valid canonical URL.", (0.50, 0.95, 0.65, 0.35, "medium"))
    if "noindex" in evidence.robots_directives:
        add("robots.noindex", "medium", "The page asks search engines not to index it; confirm intent.", (0.80, 0.95, 0.85, 0.35, "medium"))
    if evidence.word_count < 150:
        add("content.thin", "low", "The page has fewer than 150 visible words.", (0.35, 0.85, 0.50, 0.60, "low"), category="content")

    score = max(0, 100 - sum(DEDUCTIONS[finding.severity] for finding in findings))
    return score, findings


def evaluate_multiagent_page(evidence: MultiAgentPageEvidence) -> tuple[int, list[Finding]]:
    """Evaluates a page across the 6 specialized agent dimensions."""
    findings: list[Finding] = []

    def add(
        code: str,
        severity: str,
        summary: str,
        factors: tuple[float, float, float, float, str],
        category: str,
    ) -> None:
        findings.append(Finding(code, severity, summary, *factors, category=category))

    page = evidence.page
    links = evidence.link_graph
    gsc = evidence.search_console
    perf = evidence.performance

    # 1. Technical SEO Agent
    if page.status is None:
        add("status.missing", "high", "No HTTP status was recorded.", (0.60, 0.70, 0.70, 0.60, "low"), "technical")
    elif page.status >= 500:
        add("status.server_error", "critical", "The page returned a server error.", (0.95, 0.99, 1.00, 0.80, "low"), "technical")
    elif page.status >= 400:
        add("status.client_error", "high", "The page returned a client error.", (0.80, 0.99, 0.90, 0.65, "low"), "technical")
    elif 300 <= page.status < 400:
        add("status.redirect", "medium", "The crawled URL returned a redirect.", (0.45, 0.99, 0.60, 0.45, "low"), "technical")

    title = (page.title or "").strip()
    if not title:
        add("title.missing", "high", "The page has no title element.", (0.55, 0.98, 0.75, 0.25, "low"), "technical")
    elif len(title) < 15 or len(title) > 65:
        add("title.length", "low", "The title is outside the 15–65 character review range.", (0.25, 0.90, 0.40, 0.20, "low"), "technical")

    description = (page.meta_description or "").strip()
    if not description:
        add("description.missing", "medium", "The page has no meta description.", (0.40, 0.98, 0.65, 0.20, "low"), "technical")
    elif len(description) < 50 or len(description) > 170:
        add("description.length", "low", "The meta description is outside the 50–170 character review range.", (0.25, 0.90, 0.40, 0.20, "low"), "technical")

    if page.canonical_url is None:
        add("canonical.missing", "low", "The page has no valid canonical URL.", (0.50, 0.95, 0.65, 0.35, "medium"), "technical")
    if "noindex" in page.robots_directives:
        add("robots.noindex", "medium", "The page asks search engines not to index it; confirm intent.", (0.80, 0.95, 0.85, 0.35, "medium"), "technical")
    if content_requires_javascript(page):
        # High risk because the fix is how the whole site is built -- server
        # rendering or prerendering -- which is never a change to deploy
        # without a person deciding it.
        add(
            "rendering.content_requires_javascript",
            "high",
            f"Before JavaScript runs this page shows {page.server_word_count} of its {page.word_count} words. "
            "Google must render it to see the rest; crawlers that do not run scripts, including AI search, see an empty page.",
            (0.80, 0.90, 0.70, 0.80, "high"),
            "technical",
        )

    # 2. Content SEO Agent
    if not page.h1:
        add("h1.missing", "medium", "The page has no H1 heading.", (0.35, 0.98, 0.55, 0.25, "low"), "content")
    elif len(page.h1) > 1:
        add("h1.multiple", "low", "The page has multiple H1 headings.", (0.25, 0.95, 0.40, 0.25, "low"), "content")
    elif title and page.h1:
        # Stemmed, so "calculators" in the title matches "calculator" in the
        # heading. Comparing raw tokens reported a mismatch between "Free
        # Online Calculators" and "Every calculator you'll ever need", which is
        # the same subject in two grammatical numbers.
        title_tokens = similarity_tokens(title)
        h1_tokens = similarity_tokens(page.h1[0])
        if title_tokens and h1_tokens and not (title_tokens & h1_tokens):
            add("content.title_h1_mismatch", "low", "Title and H1 share no common thematic keywords.", (0.30, 0.85, 0.45, 0.30, "low"), "content")

    for stem, word in url_topic_tokens(evidence.normalized_url):
        # Both, deliberately. A word in the title but not the heading is a
        # weaker signal and a different fix; this is the case where the page
        # never says what its own address says it is.
        if stem in similarity_tokens(title) or (
            page.h1 and stem in similarity_tokens(page.h1[0])
        ):
            continue
        add(
            "content.title_omits_url_topic",
            "medium",
            f"The URL says this page is a '{word}', and neither the title nor the H1 says so.",
            (0.60, 0.90, 0.65, 0.25, "low"),
            "content",
        )
        break

    if page.h1 and page.pages_sharing_h1 >= DUPLICATE_H1_PAGE_THRESHOLD:
        add(
            "h1.duplicate_across_site",
            "medium",
            f"The H1 is the same on {page.pages_sharing_h1} pages, so it identifies none of them.",
            (0.55, 0.95, 0.60, 0.30, "low"),
            "content",
        )

    if page.word_count < 150:
        add("content.thin", "low", "The page has fewer than 150 visible words.", (0.35, 0.85, 0.50, 0.60, "low"), "content")

    # 3. Internal Linking Agent
    # If the page is not the homepage (assuming path != "/") and has 0 inbound links
    if not evidence.normalized_url.endswith("/") and links.inbound_internal_links == 0 and (page.status == 200 or page.status is None):
        add("linking.orphan_page", "high", "The page has no inbound internal links from the crawl graph.", (0.65, 0.95, 0.70, 0.35, "low"), "internal_linking")
    if links.crawl_depth >= 4:
        add("linking.deep_page", "medium", "The page is buried at crawl depth 4 or deeper from the homepage.", (0.45, 0.90, 0.50, 0.40, "low"), "internal_linking")
    if links.missing_anchor_inbound_count > 0:
        add("linking.missing_anchors", "low", f"{links.missing_anchor_inbound_count} inbound links have missing or empty anchor text.", (0.25, 0.90, 0.35, 0.20, "low"), "internal_linking")
    if links.inbound_broken_links > 0:
        add("linking.broken_links", "high", f"{links.inbound_broken_links} links point to 4xx/5xx responses.", (0.70, 0.99, 0.80, 0.30, "low"), "internal_linking")

    # 4. Keyword Opportunity Agent (from GSC)
    if gsc.striking_distance_queries > 0 or (gsc.position is not None and 10.1 <= gsc.position <= 20.0 and gsc.impressions >= 100):
        add(
            "keyword.striking_distance",
            "medium",
            "Page ranks in striking distance (positions 11–20) with high impression demand.",
            (0.70, 0.90, 0.75, 0.40, "low"),
            "keyword",
        )
    if gsc.low_ctr_queries > 0 or (gsc.position is not None and gsc.position <= 10.0 and (gsc.ctr or 0.0) < 0.02 and gsc.impressions >= 500):
        add(
            "keyword.ctr_gap",
            "high",
            "Page ranks on page 1 but achieves lower than expected CTR (< 2%).",
            (0.75, 0.92, 0.80, 0.25, "low"),
            "keyword",
        )
    if gsc.impressions >= 1000 and gsc.clicks == 0:
        add(
            "keyword.high_impression_zero_click",
            "medium",
            "High search visibility with 0 clicks in the measurement period.",
            (0.60, 0.88, 0.65, 0.35, "low"),
            "keyword",
        )

    # 5. Performance Agent (from PageSpeed / CWV)
    if perf.lcp_ms is not None:
        if perf.lcp_ms >= 4000:
            add("performance.lcp_poor", "high", f"Largest Contentful Paint ({round(perf.lcp_ms)} ms) exceeds 4.0s.", (0.65, 0.95, 0.70, 0.50, "medium"), "performance")
        elif perf.lcp_ms >= 2500:
            add("performance.lcp_needs_improvement", "medium", f"Largest Contentful Paint ({round(perf.lcp_ms)} ms) needs improvement (2.5s–4.0s).", (0.45, 0.90, 0.50, 0.40, "medium"), "performance")

    if perf.cls is not None:
        if perf.cls >= 0.25:
            add("performance.cls_poor", "high", f"Cumulative Layout Shift ({perf.cls:.3f}) exceeds 0.25 threshold.", (0.60, 0.95, 0.65, 0.45, "medium"), "performance")
        elif perf.cls >= 0.10:
            add("performance.cls_needs_improvement", "medium", f"Cumulative Layout Shift ({perf.cls:.3f}) needs improvement (0.10–0.25).", (0.40, 0.90, 0.45, 0.35, "medium"), "performance")

    if perf.inp_ms is not None:
        if perf.inp_ms >= 500:
            add("performance.inp_poor", "high", f"Interaction to Next Paint ({round(perf.inp_ms)} ms) exceeds 500 ms.", (0.60, 0.92, 0.65, 0.50, "medium"), "performance")
        elif perf.inp_ms >= 200:
            add("performance.inp_needs_improvement", "medium", f"Interaction to Next Paint ({round(perf.inp_ms)} ms) needs improvement (200–500 ms).", (0.40, 0.88, 0.45, 0.40, "medium"), "performance")

    if perf.performance_score is not None and perf.performance_score < 50:
        add("performance.score_poor", "high", f"Mobile Lighthouse performance score ({perf.performance_score}/100) is in the red zone.", (0.60, 0.95, 0.65, 0.55, "medium"), "performance")

    # 6. GEO & AI Search Visibility Agent
    has_structured_data = bool(page.structured_data)
    if not has_structured_data and (page.status == 200 or page.status is None) and "noindex" not in page.robots_directives:
        add("geo.missing_structured_data", "medium", "Page has no JSON-LD structured data for entity and citation discovery.", (0.50, 0.92, 0.55, 0.25, "low"), "geo_visibility")

    score = max(0, 100 - sum(DEDUCTIONS[finding.severity] for finding in findings))
    return score, findings
