"""Publication date normalisation for PubMed <PubDate> elements."""

from __future__ import annotations

import datetime as dt
import re

from lxml import etree

from ingestion.models import PubDate

_MONTHS = {name: i for i, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_MONTH_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.IGNORECASE)


def _month(text: str | None) -> int | None:
    if not text:
        return None
    text = text.strip()
    if text.isdigit():
        value = int(text)
        return value if 1 <= value <= 12 else None
    return _MONTHS.get(text[:3].lower())


def _valid_day(year: int, month: int, day: int) -> bool:
    try:
        dt.date(year, month, day)
    except ValueError:
        return False
    return True


def parse_pub_date(pubdate: etree._Element | None) -> PubDate | None:
    """Normalise <PubDate> into a PubDate with explicit precision, or None if unusable."""
    if pubdate is None:
        return None

    medline = pubdate.findtext("MedlineDate")
    if medline:
        year_match = _YEAR_RE.search(medline)
        if not year_match:
            return None
        year = int(year_match.group(1))
        month_match = _MONTH_RE.search(medline[year_match.end():])
        month = _month(month_match.group(1)) if month_match else None
        return PubDate(year=year, month=month, precision="month" if month else "year", raw=medline)

    year_text = pubdate.findtext("Year")
    if not year_text or not year_text.strip().isdigit():
        return None
    year = int(year_text)
    month = _month(pubdate.findtext("Month"))
    day_text = pubdate.findtext("Day")
    if month and day_text and day_text.strip().isdigit() and _valid_day(year, month, int(day_text)):
        return PubDate(year=year, month=month, day=int(day_text), precision="day")
    if month:
        return PubDate(year=year, month=month, precision="month")
    return PubDate(year=year, precision="year")
