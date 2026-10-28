"""Pure conversion of parsed records into Cypher parameter rows (easy to unit test)."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

from ingestion.authors import Authorship
from ingestion.models import PaperRecord

NOTICE_RELATIONSHIPS = {
    "RetractionIn": "RETRACTED_BY",
    "ErratumIn": "CORRECTED_BY",
    "CommentIn": "COMMENTED_IN",
    "RetractionOf": "RETRACTION_OF",
    "CommentOn": "COMMENTS_ON",
}


class BatchRows(BaseModel):
    """Everything one load transaction needs, as lists of plain dicts."""

    papers: list[dict] = Field(default_factory=list)
    journals: list[dict] = Field(default_factory=list)
    authorships: list[dict] = Field(default_factory=list)
    mentions: list[dict] = Field(default_factory=list)
    notices: dict[str, list[dict]] = Field(default_factory=dict)


def _notice_key(pmid: str | None, source: str) -> str:
    """Natural key of a notice: its PMID, or a hash of its citation text when PubMed gives no PMID."""
    if pmid:
        return f"pmid:{pmid}"
    return "src:" + hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def build_batch_rows(
    papers: list[PaperRecord],
    authorships: dict[str, list[Authorship]],
    corpus_version: str,
) -> BatchRows:
    """Flatten a batch of papers (plus their resolved authorships) into Cypher rows."""
    rows = BatchRows()
    for paper in papers:
        d = paper.pub_date
        rows.papers.append(
            {
                "pmid": paper.pmid,
                "title": paper.title,
                "abstract": paper.abstract,
                "pub_date": d.iso if d else None,
                "pub_year": d.year if d else None,
                "date_precision": d.precision if d else None,
                "pub_date_raw": d.raw if d else None,
                "doi": paper.doi,
                "publication_types": paper.publication_types,
                "retracted": paper.retracted,
                "in_corpus": True,
                "corpus_version": corpus_version,
            }
        )
        if paper.journal:
            j = paper.journal
            rows.journals.append(
                {
                    "pmid": paper.pmid,
                    "issn": j.key,
                    "issn_type": j.key_type,
                    "title": j.title,
                    "issn_print": j.issn_print,
                    "issn_electronic": j.issn_electronic,
                    "issn_linking": j.issn_linking,
                }
            )
        for a in authorships.get(paper.pmid, []):
            rows.authorships.append(a.model_dump())
        for m in paper.mesh:
            rows.mentions.append(
                {
                    "pmid": paper.pmid,
                    "ui": m.ui,
                    "name": m.name,
                    "major": m.major,
                    "qualifiers": m.qualifiers,
                }
            )
        for c in paper.corrections:
            rel = NOTICE_RELATIONSHIPS.get(c.ref_type)
            if rel:
                rows.notices.setdefault(rel, []).append(
                    {
                        "pmid_from": paper.pmid,
                        "key": _notice_key(c.pmid, c.source),
                        "pmid": c.pmid,
                        "type": c.ref_type,
                        "source": c.source,
                    }
                )
    return rows
