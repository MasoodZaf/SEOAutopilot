"""The H1 repair, exercised on the markup shape it was written against.

The fixture is TheCalcHive's real page structure: one hero H1 carrying markup,
duplicated across every calculator page, with the page's own subject present
only in the title.
"""

import pytest

from app.domain.h1_repair import (
    H1RepairError,
    derive_heading,
    is_site_root,
    plan_repair,
    repair_document,
)

HERO = "<h1>Every calculator<br>you'll ever <em>need</em></h1>"

PAGE = f"""<!doctype html>
<html><head>
<title>EMI Calculator — Free Online Tool | CalcHive</title>
<link rel="canonical" href="https://thecalchive.com/emi-calculator">
</head><body>
<div id="home-view"><div class="hero">
  <div class="hero-tag">100% Free · No Signup</div>
  {HERO}
  <p class="hero-sub">From BMI to EMI, GPA to Zodiac.</p>
</div></div>
<div class="calc-view" id="calc-view"></div>
</body></html>"""

URL = "https://thecalchive.com/emi-calculator"


def test_the_heading_is_the_topic_segment_of_the_title() -> None:
    assert derive_heading("EMI Calculator — Free Online Tool | CalcHive") == "EMI Calculator"
    assert derive_heading("Zodiac & Birth Chart — Free Online Tool | CalcHive") == "Zodiac & Birth Chart"
    assert derive_heading("Number to Words — Free Online Tool | CalcHive") == "Number to Words"


def test_a_hyphenated_word_is_not_mistaken_for_a_separator() -> None:
    assert derive_heading("Built-in Shelving Costs | Example") == "Built-in Shelving Costs"


def test_a_title_with_no_separator_is_used_whole() -> None:
    assert derive_heading("Mortgage Calculator") == "Mortgage Calculator"


def test_the_repair_replaces_the_heading_and_leaves_the_rest_byte_identical() -> None:
    repair = plan_repair(PAGE, "EMI Calculator — Free Online Tool | CalcHive", URL)

    assert repair.previous_heading == "Every calculator you'll ever need"
    assert repair.heading == "EMI Calculator"
    assert "<h1>EMI Calculator</h1>" in repair.after_content
    assert HERO not in repair.after_content
    # Everything outside the one element survives untouched.
    assert repair.after_content.replace("<h1>EMI Calculator</h1>", HERO) == PAGE


def test_an_ampersand_in_the_heading_is_escaped_once() -> None:
    document = "<html><h1>old heading here</h1></html>"
    repair = repair_document(document, "Zodiac & Birth Chart — Free Tool")

    assert "<h1>Zodiac &amp; Birth Chart</h1>" in repair.after_content
    assert "&amp;amp;" not in repair.after_content


def test_the_front_page_is_refused() -> None:
    # The hero is the front page's headline and the only place it is visible.
    for url in ("https://thecalchive.com/", "https://thecalchive.com", "https://thecalchive.com/index.html"):
        assert is_site_root(url)
        with pytest.raises(H1RepairError, match="h1_repair_refuses_site_root"):
            plan_repair(PAGE, "CalcHive — Free Online Calculators", url)


def test_a_page_with_two_h1_elements_is_refused() -> None:
    document = "<html><h1>first</h1><h1>second</h1></html>"
    with pytest.raises(H1RepairError, match="h1_repair_multiple_h1_elements"):
        repair_document(document, "Topic — Brand")


def test_a_page_with_no_h1_is_refused() -> None:
    with pytest.raises(H1RepairError, match="h1_repair_no_h1_element"):
        repair_document("<html><h2>not a heading</h2></html>", "Topic — Brand")


def test_a_heading_that_is_already_correct_is_refused() -> None:
    document = "<html><h1>EMI Calculator</h1></html>"
    with pytest.raises(H1RepairError, match="h1_repair_already_correct"):
        repair_document(document, "EMI Calculator — Free Online Tool | CalcHive")


def test_a_title_that_yields_nothing_usable_is_refused() -> None:
    for title in ("", "  ", "| CalcHive", "a — b"):
        with pytest.raises(H1RepairError, match="h1_repair_title_yields_no_heading"):
            repair_document(PAGE, title)


def test_the_repair_is_deterministic() -> None:
    first = plan_repair(PAGE, "EMI Calculator — Free Online Tool | CalcHive", URL)
    second = plan_repair(PAGE, "EMI Calculator — Free Online Tool | CalcHive", URL)
    assert first == second
