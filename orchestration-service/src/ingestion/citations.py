"""Citations from two sources (XML ReferenceList and elink), reconciled into CITES edges."""

from __future__ import annotations

from typing import Literal

from lxml import etree
from pydantic import BaseModel

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)


def parse_elink_refs(xml: bytes) -> dict[str, list[str]]:
    """elink (pubmed_pubmed_refs) XML → {source PMID: [cited PMIDs]}."""
    root = etree.fromstring(xml, parser=_PARSER)
    result: dict[str, list[str]] = {}
    for linkset in root.findall("LinkSet"):
        source = linkset.findtext("IdList/Id")
        if not source:
            continue
        targets = linkset.xpath("./LinkSetDb[LinkName='pubmed_pubmed_refs']/Link/Id/text()")
        result[source] = [t.strip() for t in targets]
    return result


class CitationEdge(BaseModel):
    """One intra-corpus (:Paper)-[:CITES]->(:Paper) edge."""

    src: str
    dst: str
    source: Literal["xml", "elink", "both"]


class CitationReport(BaseModel):
    """Edges plus the per-source numbers the brief asks us to report."""

    edges: list[CitationEdge]
    xml_internal: int
    elink_internal: int
    both: int
    xml_only: int
    elink_only: int
    dangling_xml: int
    dangling_elink: int


def _pairs(refs: dict[str, list[str]]) -> set[tuple[str, str]]:
    return {(src, dst) for src, targets in refs.items() for dst in targets if src != dst}


def reconcile_citations(xml_refs: dict[str, list[str]], elink_refs: dict[str, list[str]],
                        corpus: set[str]) -> CitationReport:
    """Merge both sources; keep only edges with both ends in the corpus; count the rest."""
    xml_all, elink_all = _pairs(xml_refs), _pairs(elink_refs)
    inside = lambda pairs: {(s, d) for s, d in pairs if s in corpus and d in corpus}  # noqa: E731
    xml_in, elink_in = inside(xml_all), inside(elink_all)

    edges = []
    for src, dst in sorted(xml_in | elink_in, key=lambda e: (int(e[0]), int(e[1]))):
        in_xml, in_elink = (src, dst) in xml_in, (src, dst) in elink_in
        edges.append(CitationEdge(src=src, dst=dst,
                                  source="both" if in_xml and in_elink else "xml" if in_xml else "elink"))
    return CitationReport(
        edges=edges,
        xml_internal=len(xml_in), elink_internal=len(elink_in),
        both=len(xml_in & elink_in), xml_only=len(xml_in - elink_in), elink_only=len(elink_in - xml_in),
        dangling_xml=len(xml_all - xml_in), dangling_elink=len(elink_all - elink_in),
    )
