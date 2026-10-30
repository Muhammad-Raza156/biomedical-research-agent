"""Write eval/author_pairs.csv with candidate pairs to label by hand (column `same`: y/n)."""

import csv
import glob
import json
import random
import sys
from itertools import combinations
from pathlib import Path

from ingestion.authors import resolve_authors
from ingestion.models import PaperRecord

staging = Path(sys.argv[1])            # e.g. a local copy of /data/staging/v1
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("../eval/author_pairs.csv")
papers = [PaperRecord.model_validate(p) for f in sorted(glob.glob(str(staging / "parsed" / "batch_*.json")))
          for p in json.loads(Path(f).read_text(encoding="utf-8"))["papers"]]
resolution = resolve_authors(papers)
by_occ = {(a.pmid, a.position): a for a in resolution.authorships}
by_key: dict[str, list] = {}
for paper in papers:
    for author in paper.authors:
        entity = by_occ.get((paper.pmid, author.position))
        if entity and not entity.author_id.startswith("collective:"):
            key = next(e.name_key for e in resolution.authors if e.author_id == entity.author_id)
            by_key.setdefault(key, []).append((paper, author, entity))

merged, separate = [], []
for key, occs in by_key.items():
    for (p1, a1, e1), (p2, a2, e2) in combinations(occs, 2):
        if p1.pmid != p2.pmid:
            (merged if e1.author_id == e2.author_id else separate).append((key, p1, a1, e1, p2, a2, e2))
random.seed(42)
sample = random.sample(merged, min(15, len(merged))) + random.sample(separate, min(15, len(separate)))
with out.open("w", newline="", encoding="utf-8") as handle:
    w = csv.writer(handle)
    w.writerow(["name_key", "pmid_a", "name_a", "orcid_a", "affil_a", "pmid_b", "name_b", "orcid_b",
                "affil_b", "predicted_same", "rule_b", "same"])
    for key, p1, a1, e1, p2, a2, e2 in sample:
        # name_as_written lives on the Authorship (e1/e2); orcid/affiliations on the raw AuthorRecord (a1/a2).
        w.writerow([key, p1.pmid, e1.name_as_written, a1.orcid, "; ".join(a1.affiliations)[:150],
                    p2.pmid, e2.name_as_written, a2.orcid, "; ".join(a2.affiliations)[:150],
                    "y" if e1.author_id == e2.author_id else "n", e2.match_rule, ""])
print(f"wrote {len(sample)} pairs to {out}; fill the `same` column with y/n")
