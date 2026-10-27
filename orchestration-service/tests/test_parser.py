from pathlib import Path

import pytest

from ingestion.parser import MalformedXmlError, normalise_orcid, parse_pubmed_xml

FIXTURES = Path(__file__).parent / "fixtures" / "pubmed"


def parse_one(pmid: str):
    result = parse_pubmed_xml((FIXTURES / f"{pmid}.xml").read_bytes())
    assert result.skipped == []
    assert len(result.papers) == 1
    return result.papers[0]


def test_structured_abstract_orcid_journal_and_references():
    paper = parse_one("41178701")
    assert paper.pmid == "41178701"
    assert paper.title.startswith("Synergistic associations of metformin")
    assert paper.abstract.startswith("BACKGROUND:")          # labels are kept as prefixes
    assert "METHODS:" in paper.abstract
    assert paper.pub_date.iso == "2026-02" and paper.pub_date.precision == "month"
    assert paper.doi == "10.1111/dom.70267"
    assert paper.journal.key == "1462-8902" and paper.journal.key_type == "issn_l"
    assert paper.journal.issn_electronic == "1463-1326"
    first = paper.authors[0]
    assert (first.position, first.last_name, first.fore_name, first.orcid) == (1, "Henney", "Alex E", "0000-0002-8066-9470")
    assert paper.authors[4].orcid == "0000-0001-9991-7369"
    assert len(paper.authors) == 7
    assert len(paper.mesh) == 17
    metformin = next(m for m in paper.mesh if m.ui == "D008687")
    assert metformin.name == "Metformin" and metformin.major is True
    assert len(paper.xml_references) == 50
    assert paper.retracted is False


def test_medline_date():
    paper = parse_one("37099785")
    assert paper.pub_date.iso == "2022-11"
    assert paper.pub_date.raw == "2022 Nov-Dec 01"
    assert paper.xml_references == []


def test_collective_author_keeps_position():
    paper = parse_one("40243198")
    assert len(paper.authors) == 15
    group = paper.authors[14]
    assert group.position == 15 and group.is_collective
    assert group.name_as_written == "DPP Research Group"
    assert group.last_name is None
    assert paper.pub_date.iso == "2025-07-01"


def test_missing_abstract_and_initials_only_names():
    paper = parse_one("33516777")
    assert paper.abstract is None
    assert paper.authors[0].fore_name == "A E"
    assert [c.ref_type for c in paper.corrections] == ["CommentOn"]


def test_retraction_notice():
    paper = parse_one("35269852")
    assert paper.retracted is True
    retraction = next(c for c in paper.corrections if c.ref_type == "RetractionIn")
    assert retraction.pmid == "40508236"
    assert paper.authors[0].fore_name == "Mónica"          # accents preserved
    assert "Retracted Publication" in paper.publication_types


def test_erratum_without_pmid_does_not_crash():
    paper = parse_one("24647047")
    erratum = next(c for c in paper.corrections if c.ref_type == "ErratumIn")
    assert erratum.pmid is None
    assert erratum.source.startswith("PLoS One. 2014")
    assert paper.pub_date.iso == "2014" and paper.pub_date.precision == "year"


def test_inline_markup_is_kept_as_text():
    xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article>
      <ArticleTitle>Effect of <i>metformin</i> on HbA<sub>1c</sub></ArticleTitle></Article>
      </MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    paper = parse_pubmed_xml(xml).papers[0]
    assert paper.title == "Effect of metformin on HbA1c"


def test_bad_records_are_skipped_with_reasons_not_raised():
    xml = b"""<PubmedArticleSet>
      <PubmedBookArticle><BookDocument><PMID>7</PMID></BookDocument></PubmedBookArticle>
      <PubmedArticle><MedlineCitation><PMID>8</PMID><Article></Article></MedlineCitation></PubmedArticle>
      <PubmedArticle><MedlineCitation><PMID>9</PMID><Article><ArticleTitle>Fine</ArticleTitle></Article>
      </MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    result = parse_pubmed_xml(xml)
    assert [p.pmid for p in result.papers] == ["9"]
    assert {(s.pmid, s.reason) for s in result.skipped} == {
        ("7", "book article (PubmedBookArticle) is not supported"),
        ("8", "missing title"),
    }


def test_malformed_document_raises():
    with pytest.raises(MalformedXmlError):
        parse_pubmed_xml(b"<PubmedArticleSet><PubmedArticle>")


@pytest.mark.parametrize(("raw", "expected"), [
    ("0000-0002-8066-9470", "0000-0002-8066-9470"),
    ("https://orcid.org/0000-0002-8066-9470", "0000-0002-8066-9470"),
    ("0000 0002 8066 947X", "0000-0002-8066-947X"),
    ("not-an-orcid", None),
    (None, None),
])
def test_normalise_orcid(raw, expected):
    assert normalise_orcid(raw) == expected
