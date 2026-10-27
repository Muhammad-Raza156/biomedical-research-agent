"""PubMed efetch XML → typed records. Pure functions, no I/O."""

from __future__ import annotations

import re

from lxml import etree

from ingestion.dates import parse_pub_date
from ingestion.models import (
    AuthorRecord,
    CorrectionRecord,
    JournalRecord,
    MeshHeadingRecord,
    PaperRecord,
    ParseResult,
    SkippedRecord,
)

_ORCID_RE = re.compile(r"(\d{4})[- ]?(\d{4})[- ]?(\d{4})[- ]?(\d{3}[\dX])")
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)


class MalformedXmlError(Exception):
    """The whole efetch document is not valid XML (permanent, do not retry)."""


def _text(el: etree._Element | None) -> str | None:
    """All text inside an element (including nested tags), whitespace-collapsed."""
    if el is None:
        return None
    value = " ".join("".join(el.itertext()).split())
    return value or None


def normalise_orcid(raw: str | None) -> str | None:
    """Return a canonical 0000-0000-0000-000X ORCID, or None if it doesn't look like one."""
    if not raw:
        return None
    match = _ORCID_RE.search(raw.upper())
    return "-".join(match.groups()) if match else None


def _abstract(article: etree._Element) -> str | None:
    parts = []
    for node in article.findall("Abstract/AbstractText"):
        text = _text(node)
        if not text:
            continue
        label = node.get("Label")
        parts.append(f"{label}: {text}" if label and label != "UNLABELLED" else text)
    return "\n".join(parts) or None


def _journal(citation: etree._Element) -> JournalRecord | None:
    journal = citation.find("Article/Journal")
    linking = citation.findtext("MedlineJournalInfo/ISSNLinking")
    nlm_id = citation.findtext("MedlineJournalInfo/NlmUniqueID")
    issn_print = issn_electronic = None
    if journal is not None:
        for issn in journal.findall("ISSN"):
            if issn.get("IssnType") == "Print":
                issn_print = issn.text
            elif issn.get("IssnType") == "Electronic":
                issn_electronic = issn.text
    for key, key_type in ((linking, "issn_l"), (issn_print, "print"),
                          (issn_electronic, "electronic"), (nlm_id and f"nlm:{nlm_id}", "nlm")):
        if key:
            return JournalRecord(key=key, key_type=key_type,
                                 title=_text(journal.find("Title")) if journal is not None else None,
                                 issn_linking=linking, issn_print=issn_print,
                                 issn_electronic=issn_electronic)
    return None


def _authors(article: etree._Element) -> list[AuthorRecord]:
    authors: list[AuthorRecord] = []
    for author in article.findall("AuthorList/Author"):
        if author.get("ValidYN", "Y") == "N":
            continue  # PubMed's own "this author entry is invalid" flag
        authors.append(AuthorRecord(
            position=len(authors) + 1,
            last_name=author.findtext("LastName"),
            fore_name=author.findtext("ForeName"),
            initials=author.findtext("Initials"),
            collective_name=_text(author.find("CollectiveName")),
            orcid=normalise_orcid(author.findtext("Identifier[@Source='ORCID']")),
            affiliations=[a for a in (_text(x) for x in author.findall("AffiliationInfo/Affiliation")) if a],
        ))
    return authors


def _mesh(citation: etree._Element) -> list[MeshHeadingRecord]:
    headings = []
    for heading in citation.findall("MeshHeadingList/MeshHeading"):
        descriptor = heading.find("DescriptorName")
        if descriptor is None or not descriptor.get("UI"):
            continue
        qualifiers = heading.findall("QualifierName")
        major = descriptor.get("MajorTopicYN") == "Y" or any(q.get("MajorTopicYN") == "Y" for q in qualifiers)
        headings.append(MeshHeadingRecord(ui=descriptor.get("UI"), name=_text(descriptor) or "",
                                          major=major, qualifiers=[_text(q) or "" for q in qualifiers]))
    return headings


def _doi(article_el: etree._Element, citation: etree._Element) -> str | None:
    ids = article_el.xpath("./PubmedData/ArticleIdList/ArticleId[@IdType='doi']/text()")
    if ids:
        return ids[0].strip()
    eloc = citation.findtext("Article/ELocationID[@EIdType='doi']")
    return eloc.strip() if eloc else None


def _references(article_el: etree._Element, pmid: str) -> list[str]:
    ids = article_el.xpath(
        "./PubmedData/ReferenceList//Reference/ArticleIdList/ArticleId[@IdType='pubmed']/text()")
    seen: dict[str, None] = {}
    for ref in (i.strip() for i in ids):
        if ref.isdigit() and ref != pmid:
            seen[ref] = None  # dict keeps first-seen order and removes duplicates
    return list(seen)


def _corrections(citation: etree._Element) -> list[CorrectionRecord]:
    return [CorrectionRecord(ref_type=c.get("RefType", ""), pmid=c.findtext("PMID"),
                             source=_text(c.find("RefSource")) or "")
            for c in citation.findall("CommentsCorrectionsList/CommentsCorrections")]


def _parse_article(article_el: etree._Element) -> PaperRecord:
    citation = article_el.find("MedlineCitation")
    pmid = citation.findtext("PMID")
    article = citation.find("Article")
    title = _text(article.find("ArticleTitle")) if article is not None else None
    if not title:
        raise ValueError("missing title")
    return PaperRecord(
        pmid=pmid,
        title=title,
        abstract=_abstract(article),
        pub_date=parse_pub_date(article.find("Journal/JournalIssue/PubDate")),
        doi=_doi(article_el, citation),
        publication_types=[t for t in (_text(x) for x in article.findall("PublicationTypeList/PublicationType")) if t],
        journal=_journal(citation),
        authors=_authors(article),
        mesh=_mesh(citation),
        xml_references=_references(article_el, pmid),
        corrections=_corrections(citation),
    )


def parse_pubmed_xml(xml: bytes) -> ParseResult:
    """Parse one efetch response. Bad records are skipped with a reason; never raises per record."""
    try:
        root = etree.fromstring(xml, parser=_PARSER)
    except etree.XMLSyntaxError as exc:
        raise MalformedXmlError(str(exc)) from exc

    result = ParseResult()
    for book in root.findall("PubmedBookArticle"):
        result.skipped.append(SkippedRecord(pmid=book.findtext(".//PMID"),
                                            reason="book article (PubmedBookArticle) is not supported"))
    for article_el in root.findall("PubmedArticle"):
        pmid = article_el.findtext("MedlineCitation/PMID")
        if not pmid:
            result.skipped.append(SkippedRecord(pmid=None, reason="missing PMID"))
            continue
        try:
            result.papers.append(_parse_article(article_el))
        except Exception as exc:  # one bad record must never sink the batch
            result.skipped.append(SkippedRecord(pmid=pmid, reason=str(exc) or repr(exc)))
    return result
