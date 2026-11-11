"""Expected strings follow the published online-video examples of each style guide
(APA 7 §10.12, MLA 9 'YouTube video', Chicago 17 14.267 / 15.53, Cite Them Right 'YouTube')."""

import pytest

from app.citations.formatter import (
    apa,
    chicago_author_date,
    chicago_notes,
    format_all,
    harvard,
    mla,
    parse_date,
    split_name,
)
from app.models import CitationMetadata

URL = "https://www.youtube.com/watch?v=kBdfcR-8hEY"
TITLE = "Justice: What's The Right Thing To Do? Episode 01"


@pytest.fixture
def org():
    return CitationMetadata(creator="Harvard University", title=TITLE, published="2009-09-04", platform="YouTube",
                            url=URL, accessed="2026-10-26", duration_seconds=3290, source_type="youtube_video")


@pytest.fixture
def person():
    return CitationMetadata(creator="Michael J. Sandel", creator_is_person=True, channel="Harvard University",
                            title="Justice: Episode 01", published="2009-09-04", platform="YouTube", url=URL,
                            accessed="2026-10-26", duration_seconds=3290, source_type="youtube_video")


def test_apa_org(org):
    c = apa(org)
    assert c.reference_text == f"Harvard University. (2009, September 4). {TITLE} [Video]. YouTube. {URL}"
    assert [r.italic for r in c.reference] == [False, True, False]
    assert c.reference[1].text == TITLE
    assert c.in_text == "(Harvard University, 2009)"


def test_apa_person_with_screen_name(person):
    c = apa(person)
    assert c.reference_text == f"Sandel, M. J. [Harvard University]. (2009, September 4). Justice: Episode 01 [Video]. YouTube. {URL}"
    assert c.in_text == "(Sandel, 2009)"


def test_apa_missing_author_and_date():
    m = CitationMetadata(title="How Vaccines Work", platform="YouTube", url=URL)
    c = apa(m)
    assert c.reference_text == f"How Vaccines Work [Video]. (n.d.). YouTube. {URL}"
    assert c.in_text == "(How Vaccines Work, n.d.)"


def test_mla_org(org):
    c = mla(org)
    assert c.reference_text == f"“{TITLE}.” YouTube, uploaded by Harvard University, 4 Sept. 2009, www.youtube.com/watch?v=kBdfcR-8hEY."
    assert any(r.italic and r.text == "YouTube" for r in c.reference)


def test_mla_person(person):
    c = mla(person)
    assert c.reference_text == ("Sandel, Michael J. “Justice: Episode 01.” YouTube, uploaded by Harvard University, "
                                "4 Sept. 2009, www.youtube.com/watch?v=kBdfcR-8hEY.")
    assert c.in_text == "(Sandel)"


def test_mla_question_title_and_access_date():
    m = CitationMetadata(title="Why Do We Sleep?", platform="YouTube", url=URL, accessed="2026-10-26")
    c = mla(m)
    assert c.reference_text == "“Why Do We Sleep?” YouTube, www.youtube.com/watch?v=kBdfcR-8hEY. Accessed 26 Oct. 2026."


def test_chicago_notes(org, person):
    c = chicago_notes(org)
    assert c.reference_text == f"Harvard University. “{TITLE}.” YouTube video, 54:50. September 4, 2009. {URL}."
    assert c.note == f"Harvard University, “{TITLE},” YouTube video, 54:50, September 4, 2009, {URL}."
    p = chicago_notes(person)
    assert p.reference_text == f"Sandel, Michael J. “Justice: Episode 01.” YouTube video, 54:50. Posted by Harvard University, September 4, 2009. {URL}."
    assert p.note == f"Michael J. Sandel, “Justice: Episode 01,” YouTube video, 54:50, posted by Harvard University, September 4, 2009, {URL}."
    assert p.in_text == "Short note: Sandel, “Justice.”"


def test_chicago_author_date(org):
    c = chicago_author_date(org)
    assert c.reference_text == f"Harvard University. 2009. “{TITLE}.” YouTube video, 54:50. September 4, 2009. {URL}."
    assert c.in_text == "(Harvard University 2009)"


def test_harvard(org, person):
    assert harvard(org).reference_text == f"Harvard University (2009) {TITLE}. Available at: {URL} (Accessed: 26 October 2026)."
    assert harvard(person).reference_text.startswith("Sandel, M.J. (2009) Justice: Episode 01.")
    with_medium = harvard(org.model_copy(update={"harvard_include_medium": True}))
    assert with_medium.reference_text.startswith(f"Harvard University (2009) {TITLE} [Video]. YouTube. Available at:")
    nodate = harvard(CitationMetadata(title="Clip", url=URL, accessed="2026-10-26"))
    assert nodate.reference_text == f"Clip (no date). Available at: {URL} (Accessed: 26 October 2026)."


def test_uploaded_file_never_gets_url():
    m = CitationMetadata(title="Week 3 lecture", source_type="uploaded_file", url="https://should-not-appear.example")
    s = format_all(m)
    for c in s.citations:
        assert "should-not-appear" not in c.reference_text
        assert "should-not-appear" not in (c.note or "")
    assert any("uploaded file" in w for w in s.warnings)
    assert apa(m).reference_text == "Week 3 lecture [Unpublished recording]. (n.d.)."


def test_format_all_warnings_and_access_default():
    s = format_all(CitationMetadata())
    assert len(s.citations) == 5
    assert any("No creator" in w for w in s.warnings)
    assert any("publication date" in w for w in s.warnings)
    assert any("title" in w.lower() for w in s.warnings)


def test_year_only_and_month_dates():
    assert parse_date("2019") == (2019, None, None)
    assert parse_date("2019-03") == (2019, 3, None)
    assert parse_date("2019-13-01") == (2019, None, None)
    assert parse_date("March 2019") == (None, None, None)
    m = CitationMetadata(creator="NASA", title="Launch", published="2019-03", platform="YouTube", url=URL)
    assert "(2019, March)" in apa(m).reference_text
    assert "Mar. 2019" in mla(m).reference_text


def test_name_splitting():
    assert split_name("Jane Q. Smith") == ("Smith", ["Jane", "Q."], "")
    assert split_name("Ludwig van Beethoven") == ("van Beethoven", ["Ludwig"], "")
    assert split_name("Martin Luther King Jr.") == ("King", ["Martin", "Luther"], "Jr.")
    assert split_name("Smith, Jane") == ("Smith", ["Jane"], "")
    assert split_name("Plato") == ("Plato", [], "")


def test_short_title_never_ends_on_a_function_word():
    from app.citations.formatter import short_title

    assert short_title("History and development of cell theory | Cells | MCAT | Khan Academy") == "History and development"
    assert short_title("The Rise and Fall of Rome") == "The Rise and Fall"
    assert short_title("Justice: Episode 01") == "Justice"
