import pytest
from lxml import etree

from ingestion.dates import parse_pub_date


def el(xml: str):
    return etree.fromstring(xml)


@pytest.mark.parametrize(
    ("xml", "iso", "precision"),
    [
        ("<PubDate><Year>2022</Year><Month>Feb</Month><Day>28</Day></PubDate>", "2022-02-28", "day"),
        ("<PubDate><Year>2021</Year><Month>Mar</Month></PubDate>", "2021-03", "month"),
        ("<PubDate><Year>2021</Year><Month>03</Month></PubDate>", "2021-03", "month"),
        ("<PubDate><Year>2014</Year></PubDate>", "2014", "year"),
        ("<PubDate><MedlineDate>2022 Nov-Dec 01</MedlineDate></PubDate>", "2022-11", "month"),
        ("<PubDate><MedlineDate>2009 Spring</MedlineDate></PubDate>", "2009", "year"),
        ("<PubDate><MedlineDate>2009-2010</MedlineDate></PubDate>", "2009", "year"),
        ("<PubDate><Year>2020</Year><Season>Winter</Season></PubDate>", "2020", "year"),
    ],
)
def test_dates_keep_their_precision(xml, iso, precision):
    parsed = parse_pub_date(el(xml))
    assert parsed is not None
    assert parsed.iso == iso
    assert parsed.precision == precision


def test_medline_date_keeps_raw_text():
    parsed = parse_pub_date(el("<PubDate><MedlineDate>2022 Nov-Dec 01</MedlineDate></PubDate>"))
    assert parsed.raw == "2022 Nov-Dec 01"


def test_unparseable_or_missing_dates_return_none():
    assert parse_pub_date(None) is None
    assert parse_pub_date(el("<PubDate><MedlineDate>unknown</MedlineDate></PubDate>")) is None


def test_invalid_day_falls_back_to_month():
    parsed = parse_pub_date(el("<PubDate><Year>2021</Year><Month>Feb</Month><Day>31</Day></PubDate>"))
    assert parsed.iso == "2021-02" and parsed.precision == "month"
