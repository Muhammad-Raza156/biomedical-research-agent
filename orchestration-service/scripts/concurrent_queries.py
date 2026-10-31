"""20 concurrent POST /queries; checks every stream ends cleanly and measures agent-run overlap.

Usage: python scripts/concurrent_queries.py http://localhost:8000 20
"""

import asyncio
import json
import sys

import httpx

QUESTIONS = ["Which journal published PMID 19564453?", "Which journal published PMID 35269852, and has it been retracted?",
             "Who are the authors of PMID 33516777?", "Which journal published PMID 21241523?"]


async def one(api: httpx.AsyncClient, i: int) -> dict:
    query_id = (await api.post("/queries", json={"question": QUESTIONS[i % len(QUESTIONS)]})).json()["query_id"]
    events = []
    async with api.stream("GET", f"/queries/{query_id}/stream", timeout=None) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    steps = [e["step"] for e in events]
    return {"query_id": query_id, "events": len(events), "terminal": events[-1]["type"] if events else None,
            "steps_ok": steps == sorted(steps) and len(steps) == len(set(steps)),
            "start": events[0]["ts"] if events else None, "end": events[-1]["ts"] if events else None}


async def main(base: str, n: int) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=60) as api:
        results = await asyncio.gather(*(one(api, i) for i in range(n)))
    for r in results:
        print(r)
    spans = sorted((r["start"], r["end"]) for r in results if r["start"])
    overlap = max(sum(1 for s, e in spans if s <= t < e) for t, _ in spans)
    print(f"\ncompleted: {sum(r['terminal'] == 'answer' for r in results)}/{n}; "
          f"steps ok: {sum(r['steps_ok'] for r in results)}/{n}; max simultaneous agent runs: {overlap}")


asyncio.run(main(sys.argv[1], int(sys.argv[2])))
