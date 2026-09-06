"""Putting the subject word back into a title and heading, or refusing to.

This repair rewrites two spans of somebody's live page at once, so the cases
worth pinning are the ones where a plausible rule would produce something a
person would not have written: "Debt Payoff Planner Calculator",
"Zodiac & Birth Calculator", a title long enough for Google to truncate. Each of
those is a refusal or a different shape of edit, and none of them is a judgement
call made at deploy time.
"""

import pytest

from app.domain.title_repair import (
    MAX_TITLE_LENGTH,
    compose_name,
    plan_repair,
    repair_document,
    slug_topic,
)

SUFFIX = " — Free Online Tool | CalcHive"


def page(title: str, heading: str, *, extra: str = "") -> str:
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        f"  <title>{title}</title>\n"
        '  <meta name="description" content="untouched">\n'
        f"{extra}"
        "</head>\n<body>\n  <header><a href=\"/\">CalcHive</a></header>\n"
        f"  <h1>{heading}</h1>\n"
        "  <p>Body copy that must survive byte for byte.</p>\n"
        "</body>\n</html>\n"
    )


# --- what a slug is allowed to claim ---------------------------------------


def test_only_a_compounded_slug_claims_a_subject() -> None:
    assert slug_topic("https://x.com/networth-calculator") == "calculator"
    assert slug_topic("https://x.com/a/b/emi-calculator.html") == "calculator"
    # A single word names a section. `/about` titled "Our Story" is not a defect.
    assert slug_topic("https://x.com/about") is None
    assert slug_topic("https://x.com/") is None


# --- how a name is rewritten ------------------------------------------------


def test_a_word_meaning_the_same_job_is_replaced_not_stacked() -> None:
    """"Debt Payoff Planner Calculator" is what appending blindly produces."""
    assert compose_name("Debt Payoff Planner", "calculator") == (
        "Debt Payoff Calculator",
        "replaced",
    )
    assert compose_name("Net Worth Tracker", "calculator") == (
        "Net Worth Calculator",
        "replaced",
    )


def test_a_plain_subject_gets_the_word_appended() -> None:
    assert compose_name("Ideal Weight", "calculator") == (
        "Ideal Weight Calculator",
        "appended",
    )


def test_a_subject_people_search_for_is_not_overwritten() -> None:
    """A converter is not a calculator wearing a different hat.

    "Unit Converter" -> "Unit Calculator" would trade a term with real search
    demand for a guess, so `converter` is deliberately outside the
    interchangeable set and the name is appended to instead.
    """
    assert compose_name("Unit Converter", "calculator") == (
        "Unit Converter Calculator",
        "appended",
    )


def test_a_name_already_carrying_the_word_is_left_alone() -> None:
    assert compose_name("EMI Calculator", "calculator") is None
    assert compose_name("Free Calculator Tools", "calculator") is None


# --- planning ---------------------------------------------------------------


def test_a_page_whose_title_omits_its_own_subject_is_repaired() -> None:
    plan = plan_repair(
        page(f"Net Worth Tracker{SUFFIX}", "Net Worth Tracker"),
        "https://thecalchive.com/networth-calculator",
    )

    assert plan.applied
    assert plan.reason == "title_repair_replaced"
    assert plan.title_after == f"Net Worth Calculator{SUFFIX}"
    assert plan.heading_after == "Net Worth Calculator"


def test_the_boilerplate_suffix_survives_verbatim() -> None:
    plan = plan_repair(
        page(f"Ideal Weight{SUFFIX}", "Ideal Weight"),
        "https://thecalchive.com/ideal-calculator",
    )
    assert plan.title_after.endswith(SUFFIX)
    assert plan.title_after == f"Ideal Weight Calculator{SUFFIX}"


def test_the_front_page_is_refused_first() -> None:
    """A brand name is not a slug's to rewrite, and the root has no slug."""
    for url in ("https://thecalchive.com/", "https://thecalchive.com"):
        plan = plan_repair(page(f"CalcHive{SUFFIX}", "CalcHive"), url)
        assert plan.reason == "title_repair_refuses_site_root"


def test_a_title_that_would_be_truncated_is_refused() -> None:
    """Adding a word people search for is not worth losing the ones already read."""
    long_name = "Compound Interest And Savings Growth Projection"
    plan = plan_repair(
        page(f"{long_name}{SUFFIX}", long_name),
        "https://thecalchive.com/compound-calculator",
    )

    assert not plan.applied
    assert plan.reason == "title_repair_title_too_long"
    assert len(f"{long_name} Calculator{SUFFIX}") > MAX_TITLE_LENGTH


def test_a_heading_that_has_drifted_from_the_title_needs_a_person() -> None:
    """One change across two spans is only sound while they agree.

    Rewriting both here would silently discard whatever the heading said.
    """
    plan = plan_repair(
        page(f"Net Worth Tracker{SUFFIX}", "Work out what you are worth"),
        "https://thecalchive.com/networth-calculator",
    )

    assert not plan.applied
    assert plan.reason == "title_repair_h1_does_not_match_title_name"


def test_more_than_one_heading_is_refused() -> None:
    document = page(f"Ideal Weight{SUFFIX}", "Ideal Weight").replace(
        "</body>", "  <h1>Second</h1>\n</body>"
    )
    plan = plan_repair(document, "https://thecalchive.com/ideal-calculator")

    assert plan.reason == "title_repair_needs_exactly_one_h1"


def test_a_page_that_already_says_it_is_refused() -> None:
    plan = plan_repair(
        page(f"EMI Calculator{SUFFIX}", "EMI Calculator"),
        "https://thecalchive.com/emi-calculator",
    )
    assert plan.reason == "title_repair_topic_already_present"


# --- applying ---------------------------------------------------------------


def test_the_edit_touches_two_spans_and_nothing_else() -> None:
    before = page(f"Net Worth Tracker{SUFFIX}", "Net Worth Tracker")
    repair = repair_document(before, "https://thecalchive.com/networth-calculator")

    assert repair.applied
    assert f"<title>Net Worth Calculator{SUFFIX}</title>" in repair.after
    assert "<h1>Net Worth Calculator</h1>" in repair.after
    # Everything else, character for character.
    assert repair.after.replace(
        f"Net Worth Calculator{SUFFIX}", f"Net Worth Tracker{SUFFIX}"
    ).replace("<h1>Net Worth Calculator</h1>", "<h1>Net Worth Tracker</h1>") == before
    assert '<meta name="description" content="untouched">' in repair.after
    assert "Body copy that must survive byte for byte." in repair.after


def test_attributes_on_the_edited_elements_are_kept() -> None:
    before = page(f"Ideal Weight{SUFFIX}", "Ideal Weight").replace(
        "<h1>", '<h1 class="hero" id="top">'
    )
    repair = repair_document(before, "https://thecalchive.com/ideal-calculator")

    assert repair.applied
    assert '<h1 class="hero" id="top">Ideal Weight Calculator</h1>' in repair.after


def test_markup_characters_in_a_name_are_escaped() -> None:
    before = page("Profit &amp; Loss Tracker" + SUFFIX, "Profit &amp; Loss Tracker")
    repair = repair_document(before, "https://thecalchive.com/profit-calculator")

    assert repair.applied
    # The visible text is "Profit & Loss Tracker"; written back it must be an
    # entity again, or the page ships invalid markup.
    assert "Profit &amp; Loss Calculator" in repair.after
    assert "Profit & Loss Calculator" not in repair.after


def test_applying_twice_changes_nothing_the_second_time() -> None:
    before = page(f"Net Worth Tracker{SUFFIX}", "Net Worth Tracker")
    once = repair_document(before, "https://thecalchive.com/networth-calculator")
    twice = repair_document(once.after, "https://thecalchive.com/networth-calculator")

    assert not twice.applied
    assert twice.reason == "title_repair_topic_already_present"


def test_a_refusal_produces_no_document() -> None:
    repair = repair_document(
        page(f"EMI Calculator{SUFFIX}", "EMI Calculator"),
        "https://thecalchive.com/emi-calculator",
    )
    assert not repair.applied
    assert repair.after == ""


@pytest.mark.parametrize(
    ("slug", "title", "expected"),
    [
        ("networth-calculator", "Net Worth Tracker", "Net Worth Calculator"),
        ("debt-calculator", "Debt Payoff Planner", "Debt Payoff Calculator"),
        ("wyr-calculator", "Compatibility Quiz", "Compatibility Calculator"),
        ("pregnancy-calculator", "Pregnancy Due Date", "Pregnancy Due Date Calculator"),
        ("cgpa-calculator", "CGPA to Percentage", "CGPA to Percentage Calculator"),
        ("water-calculator", "Water Intake", "Water Intake Calculator"),
    ],
)
def test_the_pilot_sites_real_titles(slug: str, title: str, expected: str) -> None:
    """The actual pages this was written for, with their actual titles."""
    plan = plan_repair(page(f"{title}{SUFFIX}", title), f"https://thecalchive.com/{slug}")

    assert plan.applied, plan.reason
    assert plan.heading_after == expected
