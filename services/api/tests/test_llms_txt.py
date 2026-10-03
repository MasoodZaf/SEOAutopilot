"""The llms.txt renderer: deterministic, grouped, and safe against page text."""

from app.domain.llms_txt import MAX_LINKS, LlmsPage, render_llms_txt


def test_the_homepage_leads_and_sections_group_by_first_segment() -> None:
    body = render_llms_txt(
        "TheCalcHive",
        "https://calc.example",
        [
            LlmsPage("https://calc.example/tools/emi", "EMI Calculator", "Monthly loan payment."),
            LlmsPage("https://calc.example/", "TheCalcHive", "Free calculators for money and health."),
            LlmsPage("https://calc.example/about", "About", None),
            LlmsPage("https://calc.example/blog/emi-formula", "The EMI formula", "Worked through."),
        ],
    )
    assert body == (
        "# TheCalcHive\n\n"
        "> Free calculators for money and health.\n\n"
        "## Pages\n\n"
        "- [TheCalcHive](https://calc.example/): Free calculators for money and health.\n"
        "- [About](https://calc.example/about)\n\n"
        "## Blog\n\n"
        "- [The EMI formula](https://calc.example/blog/emi-formula): Worked through.\n\n"
        "## Tools\n\n"
        "- [EMI Calculator](https://calc.example/tools/emi): Monthly loan payment.\n"
    )


def test_rendering_is_deterministic_whatever_the_input_order() -> None:
    pages = [LlmsPage(f"https://a.example/p{i}", f"Page {i}", None) for i in range(5)]
    assert render_llms_txt("A", "https://a.example", pages) == render_llms_txt(
        "A", "https://a.example", list(reversed(pages))
    )


def test_page_text_cannot_break_out_of_its_list_item() -> None:
    body = render_llms_txt(
        "A",
        "https://a.example",
        [
            LlmsPage(
                "https://a.example/x (1)",
                "Evil](https://evil.example)\n## Injected",
                "line one\n- [fake](https://evil.example)",
            )
        ],
    )
    item = [line for line in body.splitlines() if line.startswith("- [")]
    assert len(item) == 1
    assert "https://a.example/x%20%281%29" in item[0]
    assert "## Injected" not in body
    assert body.count("](") == 1


def test_untitled_pages_are_skipped_and_the_list_is_bounded() -> None:
    pages = [LlmsPage(f"https://a.example/p{i:04}", f"P{i}", None) for i in range(MAX_LINKS + 50)]
    pages.append(LlmsPage("https://a.example/untitled", None, "no title"))
    body = render_llms_txt("A", "https://a.example", pages)
    assert body.count("\n- [") == MAX_LINKS
    assert "untitled" not in body


def test_long_text_is_cut_at_a_word() -> None:
    body = render_llms_txt("A", "https://a.example", [LlmsPage("https://a.example/", "Home", "word " * 100)])
    summary = body.splitlines()[2]
    assert summary.endswith("…") and len(summary) <= 202
