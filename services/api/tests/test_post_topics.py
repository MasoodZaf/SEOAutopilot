from app.domain.post_topics import SitePage, covering_page, topic_words

PAGES = [
    SitePage("https://calc.example/emi-calculator", "EMI Calculator — Free Online Tool | CalcHive"),
    SitePage("https://calc.example/bmi-calculator", ""),
]


def test_a_page_covers_a_topic_when_its_title_or_url_has_every_word():
    assert covering_page("EMI calculator", PAGES) == PAGES[0]
    assert covering_page("the BMI calculator", PAGES) == PAGES[1]  # from the URL; "the" is not a word that counts


def test_a_question_about_the_same_subject_is_a_post_of_its_own():
    assert covering_page("How is EMI calculated?", PAGES) is None
    assert covering_page("EMI vs flat interest", PAGES) is None


def test_stopwords_alone_cover_nothing():
    assert topic_words("how to do it") == set()
    assert covering_page("how to do it", PAGES) is None
