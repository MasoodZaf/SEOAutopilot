"""The brand matching behind the zero-click scorecard's branded split."""

from datetime import date

from app.services.visibility import compact, default_brand_terms, is_branded, week_start


def test_brand_terms_come_from_the_name_and_the_domain_label():
    assert default_brand_terms("TheCalcHive", "thecalchive.com") == ["thecalchive", "calchive"]
    assert default_brand_terms("WordKit", "www.wordkitapp.com") == ["wordkit", "wordkitapp"]
    # Too short to mean anything on its own.
    assert default_brand_terms("Go", "go.dev") == []


def test_a_query_is_branded_however_the_brand_is_spaced():
    brand = ["thecalchive", "calchive"]
    assert is_branded("calc hive emi", brand)
    assert is_branded("The CalcHive BMI calculator", brand)
    assert not is_branded("emi calculator", brand)
    assert compact("Calc-Hive!") == "calchive"


def test_weeks_start_on_monday():
    assert week_start(date(2026, 10, 3)) == date(2026, 9, 28)
    assert week_start(date(2026, 9, 28)) == date(2026, 9, 28)
