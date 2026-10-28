from ingestion.citations import parse_elink_refs, reconcile_citations

ELINK = b"""<eLinkResult>
  <LinkSet><DbFrom>pubmed</DbFrom><IdList><Id>1</Id></IdList>
    <LinkSetDb><DbTo>pubmed</DbTo><LinkName>pubmed_pubmed_refs</LinkName>
      <Link><Id>2</Id></Link><Link><Id>99</Id></Link></LinkSetDb></LinkSet>
  <LinkSet><DbFrom>pubmed</DbFrom><IdList><Id>3</Id></IdList></LinkSet>
</eLinkResult>"""


def test_parse_elink_one_linkset_per_source():
    assert parse_elink_refs(ELINK) == {"1": ["2", "99"], "3": []}


def test_reconcile_marks_sources_and_counts_dangling():
    xml_refs = {"1": ["2", "500"], "3": ["1"]}
    elink_refs = {"1": ["2", "99"], "4": ["3"]}
    report = reconcile_citations(xml_refs, elink_refs, corpus={"1", "2", "3", "4"})
    edges = {(e.src, e.dst): e.source for e in report.edges}
    assert edges == {("1", "2"): "both", ("3", "1"): "xml", ("4", "3"): "elink"}
    assert (report.both, report.xml_only, report.elink_only) == (1, 1, 1)
    assert (report.xml_internal, report.elink_internal) == (2, 2)
    assert (report.dangling_xml, report.dangling_elink) == (1, 1)


def test_self_citations_are_ignored():
    report = reconcile_citations({"1": ["1"]}, {}, corpus={"1"})
    assert report.edges == []
