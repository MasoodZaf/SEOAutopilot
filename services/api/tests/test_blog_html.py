import pytest

from app.domain.blog_html import BlogTemplateError, build_post_page, render_markdown
from app.domain.proposals import detect_control_directive_changes

TEMPLATE = """<!doctype html>
<html><head>
<title>EMI Calculator | TheCalcHive</title>
<meta name="description" content="Calculate your EMI.">
<link rel="canonical" href="https://thecalchive.com/emi-calculator">
<meta name="robots" content="index,follow">
<meta property="og:title" content="EMI Calculator">
<link rel="stylesheet" href="styles.css">
<script type="application/ld+json">{"@type":"SoftwareApplication"}</script>
</head><body>
<header><a href="index.html">Home</a> <a href="/about">About</a> <a href="https://x.example/">X</a></header>
<main class="wrap"><h1>EMI Calculator</h1><form id="calc"></form><script>calc()</script></main>
<footer><img src="img/logo.png"></footer>
</body></html>"""


def build(**overrides):
    arguments = {
        "template_url": "https://thecalchive.com/emi-calculator",
        "post_url": "https://thecalchive.com/blog/how-emi-works",
        "title": "How EMI works",
        "description": "What goes into an EMI.",
        "author": "Masood",
        "published_on": "2026-11-04",
        "body_markdown": "Intro.\n\n## The formula\n\n- one\n- two",
    }
    arguments.update(overrides)
    return build_post_page(TEMPLATE, **arguments)


def test_the_post_takes_the_site_shell_and_replaces_only_main():
    page = build()
    assert '<link rel="stylesheet" href="/styles.css">' in page
    assert '<header><a href="/index.html">Home</a> <a href="/about">About</a> <a href="https://x.example/">X</a></header>' in page
    assert '<img src="/img/logo.png">' in page
    assert '<main class="wrap">' in page
    assert "EMI Calculator</h1>" not in page and "calc()" not in page
    assert "<title>How EMI works</title>" in page
    assert page.count("<h1>") == 1
    assert "<h2>The formula</h2>" in page and "<ul><li>one</li><li>two</li></ul>" in page


def test_the_template_page_identity_and_directives_are_dropped():
    page = build()
    assert "Calculate your EMI." not in page
    assert "SoftwareApplication" not in page
    assert 'property="og:title" content="EMI Calculator"' not in page
    assert '"@type":"BlogPosting"' in page
    # A new page with no indexing directive at all: its own URL is canonical.
    assert detect_control_directive_changes("", page) == []


def test_model_text_cannot_add_markup():
    page = build(
        title='</title><script>alert(1)</script>',
        body_markdown='<script>alert(2)</script>\n\n[x](javascript:alert(3)) [ok](/emi-calculator) **b** *i* `<c>`',
    )
    assert "<script>alert" not in page
    assert "javascript:alert" not in page
    assert '<a href="/emi-calculator">ok</a>' in page
    assert "<strong>b</strong>" in page and "<em>i</em>" in page and "<code>&lt;c&gt;</code>" in page


def test_a_script_close_in_the_title_cannot_escape_the_json_ld():
    page = build(title="a</script><b>")
    block = page.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0]
    assert "<" not in block and "\\u003c/script\\u003e" in block


@pytest.mark.parametrize(
    ("template", "code"),
    [
        ("<html><head><title>t</title></head><body><p>no main</p></body></html>", "blog_template_needs_one_main_element"),
        ("<html><body><main></main></body></html>", "blog_template_needs_head_and_title"),
        ('<html><head><title>t</title><meta name="robots" content="noindex"></head><body><main></main></body></html>', "blog_template_is_noindex"),
    ],
)
def test_an_unusable_template_is_refused_with_a_reason(template, code):
    with pytest.raises(BlogTemplateError) as caught:
        build_post_page(
            template, template_url="https://a.example/x", post_url="https://a.example/blog/y",
            title="t", description="d", author="a", published_on="2026-11-04", body_markdown="b",
        )
    assert caught.value.code == code


def test_markdown_headings_never_become_a_second_h1():
    assert render_markdown("# Top\n\n1. a\n2. b\n\n> quoted") == (
        "<h2>Top</h2>\n<ol><li>a</li><li>b</li></ol>\n<blockquote><p>quoted</p></blockquote>"
    )


@pytest.mark.parametrize(
    ("value", "stored"),
    [("/emi-calculator", "/emi-calculator"), ("https://thecalchive.com/emi-calculator/", "/emi-calculator"), ("", "")],
)
def test_the_layout_page_setting_is_a_path_on_the_site(value, stored):
    from app.api.schemas import GovernanceSettingsUpdate

    assert GovernanceSettingsUpdate(blog_template_path=value).blog_template_path == stored


@pytest.mark.parametrize("value", ["emi-calculator", "/a/../etc/passwd", "/a b", "//evil.example/x"])
def test_anything_else_is_refused(value):
    from pydantic import ValidationError

    from app.api.schemas import GovernanceSettingsUpdate

    with pytest.raises(ValidationError):
        GovernanceSettingsUpdate(blog_template_path=value)
