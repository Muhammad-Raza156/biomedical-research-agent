from graph.rows import build_batch_rows
from ingestion.authors import Authorship
from ingestion.models import (
    CorrectionRecord,
    JournalRecord,
    MeshHeadingRecord,
    PaperRecord,
    PubDate,
)


def test_rows_flatten_a_paper():
    paper = PaperRecord(
        pmid="35269852",
        title="RETRACTED: Metformin",
        pub_date=PubDate(year=2022, month=2, day=28, precision="day"),
        publication_types=["Review", "Retracted Publication"],
        journal=JournalRecord(key="1422-0067", key_type="issn_l", title="IJMS"),
        mesh=[
            MeshHeadingRecord(
                ui="D001943",
                name="Breast Neoplasms",
                major=True,
                qualifiers=["drug therapy"],
            )
        ],
        corrections=[
            CorrectionRecord(
                ref_type="RetractionIn", pmid="40508236", source="Int J Mol Sci. 2025"
            ),
            CorrectionRecord(
                ref_type="ErratumIn", pmid=None, source="PLoS One. 2014;9(7):e103652"
            ),
        ],
    )
    authorships = {
        "35269852": [
            Authorship(
                pmid="35269852",
                position=1,
                author_id="orcid:x",
                name_as_written="Cejuela Mónica",
                match_rule="orcid",
            )
        ]
    }
    rows = build_batch_rows([paper], authorships, corpus_version="v1")

    p = rows.papers[0]
    assert (
        p["pub_date"] == "2022-02-28"
        and p["pub_year"] == 2022
        and p["date_precision"] == "day"
    )
    assert (
        p["retracted"] is True
        and p["in_corpus"] is True
        and p["corpus_version"] == "v1"
    )
    assert rows.journals == [
        {
            "pmid": "35269852",
            "issn": "1422-0067",
            "issn_type": "issn_l",
            "title": "IJMS",
            "issn_print": None,
            "issn_electronic": None,
            "issn_linking": None,
        }
    ]
    assert rows.mentions[0] == {
        "pmid": "35269852",
        "ui": "D001943",
        "name": "Breast Neoplasms",
        "major": True,
        "qualifiers": ["drug therapy"],
    }
    assert rows.authorships[0]["author_id"] == "orcid:x"
    assert rows.notices["RETRACTED_BY"][0]["key"] == "pmid:40508236"
    erratum = rows.notices["CORRECTED_BY"][0]
    assert erratum["pmid"] is None and erratum["key"].startswith("src:")
