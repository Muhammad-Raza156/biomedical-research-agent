"""Print INGESTION_REPORT.md sections from Redis (ingest:result:ingest-v1) + the pinned PMID list file."""

import json
import sys
from pathlib import Path

import redis

r = redis.Redis.from_url("redis://localhost:16379/0", decode_responses=True)
result = json.loads(r.get("ingest:result:ingest-v1"))
stats, cites = result["corpus_stats"], result["citations"]
pmids = json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else []

print("## Acceptance criteria (live)\n\n| Criterion | Minimum | Ours |\n|---|---|---|")
print(f"| Papers | 150–400 | {stats['papers']} |")
print(f"| Date span | 10 years | {stats['min_year']}–{stats['max_year']} ({stats['date_span_years']} years) |")
print(f"| Before / after {stats['cutoff_year']} | 40 / 40 | {stats['before_cutoff']} / {stats['after_cutoff']} |")
print(f"| Intra-corpus CITES | 120 | {stats['cites_total']} |")
print(f"| Longest chain | 4 | {stats['longest_chain']} |")
print(f"| Authors with 2+ papers | 25 | {stats['authors_multi_paper']} |")
print(f"| MeSH top-level trees | 3 | {len(stats['mesh_top_level_trees'])} ({', '.join(stats['mesh_top_level_trees'])}) |")
print(f"| Papers with retraction/erratum/comment | 1 | {stats['papers_with_notices']} (retracted: {stats['retracted_papers']}) |")
print("\n## Citations by source\n")
print(f"- XML ReferenceList internal edges: {cites['xml_internal']}; elink internal edges: {cites['elink_internal']}")
print(f"- both: {cites['both']}, xml only: {cites['xml_only']}, elink only: {cites['elink_only']}")
print(f"- dangling (outside corpus, skipped): xml {cites['dangling_xml']}, elink {cites['dangling_elink']}")
print("\n## Node / relationship counts\n")
print("```json\n" + json.dumps({"nodes": stats["node_counts"], "relationships": stats["relationship_counts"]}, indent=2) + "\n```")
print("\n## Skipped records\n")
print("\n".join(f"- PMID {s['pmid']}: {s['reason']}" for s in result["skipped_records"]) or "- none")
print(f"\n## Pinned PMID list ({len(pmids)})\n\n<details><summary>show</summary>\n\n{', '.join(pmids)}\n\n</details>")
