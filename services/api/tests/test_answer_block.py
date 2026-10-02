import json
import re

import pytest

from app.domain.answer_block import AnswerBlockError, AnswerItem, place_answers

PAGE = """<!doctype html>
<html>
  <head>
    <title>EMI Calculator</title>
  </head>
  <body>
    <h1>EMI Calculator</h1>
    <h2>How is EMI <em>calculated</em>?</h2>
    <div class="widget"></div>
    <h2>Pricing</h2>
    <h3>Is it free?</h3>
  </body>
</html>
"""

ANSWERS = [
    AnswerItem("How is EMI calculated?", "EMI = P × r × (1 + r)^n / ((1 + r)^n − 1), where P is the loan & r the monthly rate."),
    AnswerItem("Is it free?", "Yes. The calculator needs no account and stores nothing."),
]


def test_each_answer_lands_directly_under_its_heading_and_faq_markup_is_added():
    edit = place_answers(PAGE, "calculators/emi.html", ANSWERS)
    assert edit.placed == 2
    assert "<h2>How is EMI <em>calculated</em>?</h2>\n    <p>EMI = P × r × (1 + r)^n / ((1 + r)^n − 1), where P is the loan &amp; r the monthly rate.</p>" in edit.after
    assert "<h3>Is it free?</h3>\n    <p>Yes. The calculator needs no account and stores nothing.</p>" in edit.after
    markup = re.search(r'<script type="application/ld\+json">(.*?)</script>\n  </head>', edit.after, re.DOTALL)
    assert markup is not None
    data = json.loads(markup.group(1))
    assert data["@type"] == "FAQPage"
    assert [q["name"] for q in data["mainEntity"]] == ["How is EMI calculated?", "Is it free?"]
    # Nothing else in the file moves.
    assert edit.after.replace(markup.group(0), "  </head>").count("\n") == PAGE.count("\n") + 2


def test_jsx_answers_escape_braces_and_pass_the_markup_as_a_string():
    page = (
        "export default function Page() {\n  return (\n    <main>\n"
        "      <h2 className=\"q\">What is an EMI?</h2>\n"
        "      <Widget />\n    </main>\n  );\n}\n"
    )
    edit = place_answers(page, "app/emi/page.tsx", [AnswerItem("What is an EMI?", "It uses {braces} & <tags>.")])
    assert "<p>It uses &#123;braces&#125; &amp; &lt;tags&gt;.</p>" in edit.after
    assert '<script type="application/ld+json" dangerouslySetInnerHTML={{ __html: "' in edit.after
    assert edit.after.index("</h2>") < edit.after.index("<p>") < edit.after.index("<script")


@pytest.mark.parametrize(
    ("document", "path", "items", "code"),
    [
        (PAGE, "emi.md", ANSWERS, "answer_block_unsupported_file"),
        (PAGE, "emi.html", [AnswerItem("What is APR?", "An answer.")], "answer_heading_not_found"),
        ("<body><h2>What is {name}?</h2></body>", "page.tsx", [AnswerItem("What is ?", "An answer.")], "answer_heading_not_found"),
        (PAGE + "<h2>Is it free?</h2>", "emi.html", ANSWERS, "answer_heading_ambiguous"),
        (PAGE.replace("</head>", '<script type="application/ld+json">{"@type":"FAQPage"}</script></head>'), "emi.html", ANSWERS, "page_already_has_faq_markup"),
        ("<h2>Is it free?</h2>", "emi.html", ANSWERS[1:], "answer_block_no_head_or_body"),
        (PAGE, "emi.html", [], "answer_block_item_count_invalid"),
        (PAGE, "emi.html", [AnswerItem("Is it free?", "  ")], "answer_block_answer_invalid"),
    ],
)
def test_it_refuses_rather_than_guesses(document, path, items, code):
    with pytest.raises(AnswerBlockError) as refused:
        place_answers(document, path, items)
    assert str(refused.value) == code


def test_a_closing_script_sequence_in_an_answer_cannot_end_the_markup_early():
    edit = place_answers(PAGE, "emi.html", [AnswerItem("Is it free?", "Yes </script><script>alert(1)</script>")])
    head = edit.after[: edit.after.index("</head>")]
    assert head.count("</script>") == 1
    assert "<\\/script>" in head


def test_an_answer_takes_the_styling_of_the_page_s_own_paragraphs():
    # TheCalcHive styles its copy inline; an unstyled <p> would sit out of place.
    page = (
        "<html><head></head><body>\n"
        "  <h2 style=\"font-size: 24px\">How is BMI Calculated?</h2>\n"
        "  <p style=\"margin-bottom: 1.5rem;\">Existing copy.</p>\n"
        "  <h2>Is it free?</h2>\n  <h3>Next</h3>\n  <p class=\"x\">Not this one.</p>\n"
        "</body></html>"
    )
    edit = place_answers(page, "bmi.html", [AnswerItem("How is BMI Calculated?", "Weight over height squared."), AnswerItem("Is it free?", "Yes.")])
    assert '<h2 style="font-size: 24px">How is BMI Calculated?</h2>\n  <p style="margin-bottom: 1.5rem;">Weight over height squared.</p>' in edit.after
    # A heading comes before any paragraph, so nothing is borrowed from beyond it.
    assert "<h2>Is it free?</h2>\n  <p>Yes.</p>" in edit.after
