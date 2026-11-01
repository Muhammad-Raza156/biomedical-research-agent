"""Static and plan-based safety checks for LLM-written Cypher (layers 1 and 2 of 4)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent.ports import ExplainInfo

ALLOWED_PROCEDURES = frozenset({"db.labels", "db.relationshipTypes", "db.propertyKeys"})
_WRITE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH|GRANT|REVOKE|DENY|ALTER|RENAME|"
    r"TERMINATE|LOAD\s+CSV|USE)\b", re.IGNORECASE)
_CALL_PROC = re.compile(r"\bCALL\s+([A-Za-z_][\w.]*)\s*\(", re.IGNORECASE)
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
_STARTS = re.compile(r"^\s*(MATCH|OPTIONAL\s+MATCH|WITH|UNWIND|CALL|RETURN)\b", re.IGNORECASE)
# one left-to-right pass: whichever starts first wins, so '//' inside a string is not a comment
_STRINGS_OR_COMMENTS = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|//[^\n]*|/\*.*?\*/", re.DOTALL)
_WRITE_OPERATORS = ("Create", "Merge", "Delete", "Set", "Remove", "Foreach", "LoadCSV")


@dataclass
class SafetyResult:
    """ok + the (possibly LIMIT-amended) query, or a reason to send back to generate_cypher."""

    ok: bool
    cypher: str
    reason: str = ""


def _skeleton(cypher: str) -> str:
    """Query text with comments removed and string literals blanked (so they can't hide or trigger)."""
    return _STRINGS_OR_COMMENTS.sub(lambda m: " " if m.group().startswith("/") else "''", cypher)


def check_static(cypher: str, max_limit: int) -> SafetyResult:
    """Reject anything that could write or escape read-only use; enforce a LIMIT."""
    query = cypher.strip().rstrip(";").strip()
    skeleton = _skeleton(query)
    if ";" in skeleton:
        return SafetyResult(False, query, "only one statement is allowed (found ';')")
    if not _STARTS.match(skeleton):
        return SafetyResult(False, query, "query must start with MATCH, OPTIONAL MATCH, WITH, UNWIND, CALL or RETURN")
    write = _WRITE.search(skeleton)
    if write:
        return SafetyResult(False, query, f"write or admin clause '{write.group(1).upper()}' is not allowed")
    for proc in _CALL_PROC.findall(skeleton):
        if proc not in ALLOWED_PROCEDURES:
            return SafetyResult(False, query, f"procedure '{proc}' is not in the allow-list")
    limits = [int(n) for n in _LIMIT.findall(skeleton)]
    if any(n > max_limit for n in limits):
        return SafetyResult(False, query, f"LIMIT must be at most {max_limit}")
    if not limits:
        query = f"{query}\nLIMIT {max_limit}"
    return SafetyResult(True, query)


def check_plan(info: ExplainInfo, max_cartesian_rows: float = 100_000) -> str | None:
    """Inspect EXPLAIN: no unknown names, no write operators, no huge cartesian products."""
    if info.unknown_names:
        return ("the query uses names that are not in the graph — use only the labels, relationship types and "
                "properties from the schema: " + " ".join(info.unknown_names))
    for op in info.operators:
        if op.startswith(_WRITE_OPERATORS):
            return f"plan contains write operator '{op}'"
    if any(op.startswith("CartesianProduct") for op in info.operators) and info.max_estimated_rows > max_cartesian_rows:
        return (f"cartesian product estimated at {info.max_estimated_rows:.0f} rows; "
                "connect the patterns with a relationship or a WITH")
    return None
