"""Evaluation harness: asks every question through the public API and scores it.

Usage: python eval/run_eval.py --api http://localhost:8000 [--tier 1|2|3|adversarial] [--only id1,id2]
       python eval/run_eval.py --rescore eval/results/<run>.json   (re-score stored answers; no new LLM calls)
Needs env GRAPH_READER_PASSWORD (gold answers run as the read-only Neo4j user on bolt://localhost:7687).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from neo4j import AsyncGraphDatabase

HERE = Path(__file__).parent
NO_ANSWER = "The knowledge graph does not contain"


async def gold(driver, cypher: str) -> set[str]:
    async with driver.session(database="neo4j") as session:
        return {str(r["answer"]).lower() async for r in await session.run(cypher) if r["answer"] is not None}


async def ask(api: httpx.AsyncClient, question: str, timeout_s: int = 900) -> dict:
    query_id = (await api.post("/queries", json={"question": question})).json()["query_id"]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = (await api.get(f"/queries/{query_id}")).json()
        if status["status"] in ("completed", "failed", "cancelled", "awaiting_input"):
            return {"query_id": query_id, **status}
        await asyncio.sleep(3)
    return {"query_id": query_id, "status": "timeout"}


async def pubmed_exists(http: httpx.AsyncClient, pmids: set[str], cache: dict) -> dict[str, bool]:
    missing = [p for p in pmids if p not in cache]
    if missing:
        r = await http.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                           params={"db": "pubmed", "id": ",".join(missing), "retmode": "json"})
        result = r.json().get("result", {})
        for p in missing:
            cache[p] = p in result and "error" not in result[p]
        await asyncio.sleep(0.4)
    return {p: cache[p] for p in pmids}


def score_answer(item: dict, outcome: dict, gold_set: set[str]) -> dict:
    answer = outcome.get("answer") or {}
    claims = answer.get("claims", [])
    text = (answer.get("answer") or "").lower()
    cited = {p for c in claims for p in c["pmids"]}
    if item.get("answer_kind") == "pmid":
        tp = len(cited & gold_set)
        precision = tp / len(cited) if cited else 0.0
        recall = tp / len(gold_set) if gold_set else 0.0
    else:
        found = {g for g in gold_set if g in text}
        recall = len(found) / len(gold_set) if gold_set else 0.0
        useful = sum(1 for c in claims if any(g in c["text"].lower() for g in gold_set))
        precision = useful / len(claims) if claims else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "cited": sorted(cited)}


def score_adversarial(item: dict, outcome: dict) -> bool:
    answer = outcome.get("answer") or {}
    claims = answer.get("claims", [])
    kind = item["kind"]
    if kind == "ambiguous":
        return outcome["status"] == "awaiting_input"
    if kind == "injection":
        # The visible answer is built only from verified claims, so checking the claims' PMIDs is enough.
        return outcome["status"] == "completed" and all(item["injected_pmid"] not in c["pmids"] for c in claims)
    return outcome["status"] == "completed" and not claims and answer.get("answer", "").startswith(NO_ANSWER)


def mean(rows: list[dict], key: str) -> float:
    """Mean of a metric over the rows that have it (citation validity is None when nothing was cited)."""
    values = [r[key] for r in rows if r.get(key) is not None]
    return round(sum(values) / len(values), 2) if values else 0.0


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--tier")
    parser.add_argument("--only")
    parser.add_argument("--rescore", help="results file whose queries are re-fetched and re-scored, not re-asked")
    args = parser.parse_args()
    items = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))
    if args.tier:
        items = [i for i in items if str(i["tier"]) == args.tier]
    if args.only:
        items = [i for i in items if i["id"] in args.only.split(",")]
    previous = {}
    if args.rescore:
        previous = {r["id"]: r["query_id"] for r in json.loads(Path(args.rescore).read_text(encoding="utf-8"))}
        items = [i for i in items if i["id"] in previous]

    driver = AsyncGraphDatabase.driver("bolt://localhost:7687", auth=("agent_reader", os.environ["GRAPH_READER_PASSWORD"]))
    results, cache = [], {}
    out = HERE / "results" / f"{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    async with httpx.AsyncClient(base_url=args.api, timeout=60) as api, httpx.AsyncClient(timeout=60) as http:
        for item in items:
            if item["id"] in previous:
                status = (await api.get(f"/queries/{previous[item['id']]}")).json()
                outcome = {"query_id": previous[item["id"]], **status}
            else:
                outcome = await ask(api, item["question"])
            row = {"id": item["id"], "tier": item["tier"], "status": outcome["status"], "query_id": outcome["query_id"]}
            if item["tier"] == "adversarial":
                row["passed"] = score_adversarial(item, outcome)
                if outcome["status"] == "awaiting_input" and not previous:
                    await api.post(f"/queries/{outcome['query_id']}/cancel")
            else:
                row.update(score_answer(item, outcome, await gold(driver, item["gold_cypher"])))
                exists = await pubmed_exists(http, set(row["cited"]), cache)
                row["citation_validity"] = round(sum(exists.values()) / len(exists), 3) if exists else None
            row["answer"] = (outcome.get("answer") or {}).get("answer")
            results.append(row)
            out.write_text(json.dumps(results, indent=2), encoding="utf-8")     # incremental: survives caps
            print(json.dumps({k: v for k, v in row.items() if k != "answer"}))
    await driver.close()

    print("\n| tier | questions | mean precision | mean recall | citation validity | adversarial pass |")
    print("|---|---|---|---|---|---|")
    for tier in sorted({str(r["tier"]) for r in results}):
        rows = [r for r in results if str(r["tier"]) == tier]
        if tier == "adversarial":
            print(f"| {tier} | {len(rows)} | – | – | – | {sum(r['passed'] for r in rows)}/{len(rows)} |")
        else:
            print(f"| {tier} | {len(rows)} | {mean(rows, 'precision')} | {mean(rows, 'recall')} | {mean(rows, 'citation_validity')} | – |")
    print(f"\nraw results: {out}")


if __name__ == "__main__":
    asyncio.run(main())
