import pytest

from agent.cypher_safety import check_plan, check_static
from agent.ports import ExplainInfo


@pytest.mark.parametrize("cypher", [
    "MATCH (p:Paper) SET p.title = 'x' RETURN p",
    "MERGE (p:Paper {pmid: '1'}) RETURN p",
    "MATCH (n) DETACH DELETE n",
    "CREATE (:Paper)",
    "MATCH (p) REMOVE p.title RETURN p",
    "LOAD CSV FROM 'http://x' AS row RETURN row",
    "CALL apoc.periodic.iterate('MATCH (n) RETURN n', 'DELETE n', {})",
    "MATCH (p) RETURN p LIMIT 5; MATCH (n) DELETE n",
    "DROP INDEX paper_year",
    "MATCH (p:Paper) FOREACH (x IN [1] | SET p.a = x) RETURN p",
])
def test_writes_and_unsafe_calls_are_rejected(cypher):
    result = check_static(cypher, max_limit=200)
    assert not result.ok and result.reason


def test_keywords_inside_strings_and_comments_do_not_trigger():
    result = check_static("MATCH (p:Paper) WHERE p.title CONTAINS 'SET of patients' // CREATE\nRETURN p.pmid LIMIT 5", 200)
    assert result.ok


def test_property_names_containing_keywords_are_fine():
    assert check_static("MATCH (p:Paper) RETURN p.offset_value AS created_at LIMIT 3", 200).ok


def test_limit_is_appended_when_missing():
    result = check_static("MATCH (p:Paper) RETURN p.pmid", 200)
    assert result.ok and result.cypher.rstrip().endswith("LIMIT 200")


def test_limit_above_max_is_rejected():
    result = check_static("MATCH (p:Paper) RETURN p.pmid LIMIT 5000", 200)
    assert not result.ok and "LIMIT" in result.reason


def test_allowed_procedure_and_subquery():
    assert check_static("CALL db.labels() YIELD label RETURN label LIMIT 10", 200).ok
    assert check_static("MATCH (p:Paper) CALL (p) { MATCH (p)-[:CITES]->(q) RETURN count(q) AS c } RETURN p.pmid, c LIMIT 5", 200).ok


def test_plan_with_write_operator_or_huge_cartesian_is_rejected():
    assert check_plan(ExplainInfo(operators=["ProduceResults", "Create"], max_estimated_rows=1))
    assert check_plan(ExplainInfo(operators=["CartesianProduct", "AllNodesScan"], max_estimated_rows=5e6))
    assert check_plan(ExplainInfo(operators=["ProduceResults", "NodeByLabelScan"], max_estimated_rows=300)) is None


def test_comment_marker_inside_a_string_cannot_hide_a_write():
    result = check_static("MATCH (p:Paper) WHERE p.url = 'http://x' DETACH DELETE p", 200)
    assert not result.ok and "DETACH" in result.reason


def test_plan_naming_a_label_relationship_or_property_that_does_not_exist_is_rejected():
    # Neo4j only *warns* about unknown names; `[:BRODER*0..]` then silently matches zero-length paths only.
    info = ExplainInfo(operators=["ProduceResults"], max_estimated_rows=1,
                       unknown_names=["The relationship type `BRODER` does not exist."])
    reason = check_plan(info)
    assert reason and "BRODER" in reason and "schema" in reason
